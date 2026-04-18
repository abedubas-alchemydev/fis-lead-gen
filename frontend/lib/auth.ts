import { betterAuth } from "better-auth";
import { APIError } from "better-auth/api";
import { Pool } from "pg";

import {
  sendPasswordResetEmail,
  sendVerificationEmail as sendVerifyEmail,
  sendAdminApprovalRequestEmail,
} from "@/lib/email";

const globalForDatabase = globalThis as typeof globalThis & {
  __deshornPool?: Pool;
};

const database =
  globalForDatabase.__deshornPool ??
  new Pool({
    connectionString: process.env.DATABASE_URL
  });

if (process.env.NODE_ENV !== "production") {
  globalForDatabase.__deshornPool = database;
}

export const db = database;

const appUrl = process.env.BETTER_AUTH_URL ?? process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

export const auth = betterAuth({
  database,
  baseURL: appUrl,
  basePath: "/api/auth",
  trustedOrigins: [
    process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000",
    "http://127.0.0.1:3000"
  ],
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 8,
    maxPasswordLength: 128,
    sendResetPassword: async ({ user, url }) => {
      await sendPasswordResetEmail({ user, url });
    }
  },
  emailVerification: {
    sendOnSignUp: true,
    autoSignInAfterVerification: true,
    expiresIn: 60 * 60 * 24,
    sendVerificationEmail: async ({ user, url }) => {
      await sendVerifyEmail({ user, url });
    }
  },
  user: {
    fields: {
      emailVerified: "email_verified",
      createdAt: "created_at",
      updatedAt: "updated_at"
    },
    additionalFields: {
      role: {
        type: "string",
        required: false,
        defaultValue: "viewer",
        input: false
      },
      status: {
        type: "string",
        required: false,
        defaultValue: "pending",
        input: false,
        returned: true
      }
    }
  },
  session: {
    expiresIn: 60 * 30,
    updateAge: 60 * 5,
    fields: {
      userId: "user_id",
      expiresAt: "expires_at",
      ipAddress: "ip_address",
      userAgent: "user_agent",
      createdAt: "created_at",
      updatedAt: "updated_at"
    },
    cookieCache: {
      enabled: true,
      maxAge: 300
    }
  },
  account: {
    fields: {
      userId: "user_id",
      accountId: "account_id",
      providerId: "provider_id",
      accessToken: "access_token",
      refreshToken: "refresh_token",
      accessTokenExpiresAt: "access_token_expires_at",
      refreshTokenExpiresAt: "refresh_token_expires_at",
      idToken: "id_token",
      createdAt: "created_at",
      updatedAt: "updated_at"
    }
  },
  verification: {
    fields: {
      expiresAt: "expires_at",
      createdAt: "created_at",
      updatedAt: "updated_at"
    }
  },
  databaseHooks: {
    user: {
      create: {
        after: async (user) => {
          try {
            const admins = await database.query<{ email: string }>(
              'SELECT email FROM "user" WHERE role = $1 AND status = $2',
              ["admin", "active"]
            );
            const adminEmails = admins.rows.map((r) => r.email).filter(Boolean);
            if (adminEmails.length === 0) {
              console.warn(
                `[APPROVAL] No active admins to notify for pending signup ${user.email}`
              );
              return;
            }
            await sendAdminApprovalRequestEmail({
              newUser: {
                email: user.email,
                name: user.name,
                createdAt: user.createdAt
              },
              adminEmails,
              settingsUrl: `${appUrl}/settings/users`
            });
          } catch (err) {
            // Never block signup on notification failure.
            console.error("[APPROVAL] Failed to notify admins of pending signup:", err);
          }
        }
      }
    },
    session: {
      create: {
        before: async (session) => {
          const result = await database.query<{ status: string }>(
            'SELECT status FROM "user" WHERE id = $1',
            [session.userId]
          );
          const status = result.rows[0]?.status;
          if (status !== "active") {
            // Shared copy for pending + rejected so rejected users aren't enumerable.
            throw new APIError("FORBIDDEN", {
              message: "Your account is pending admin approval."
            });
          }
        }
      }
    }
  },
  advanced: {
    useSecureCookies: process.env.ENVIRONMENT === "production"
  },
  secret: process.env.BETTER_AUTH_SECRET
});
