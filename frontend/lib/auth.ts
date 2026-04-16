import { betterAuth } from "better-auth";
import { Pool } from "pg";

import {
  sendPasswordResetEmail,
  sendVerificationEmail as sendVerifyEmail,
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
  advanced: {
    useSecureCookies: process.env.ENVIRONMENT === "production"
  },
  secret: process.env.BETTER_AUTH_SECRET
});
