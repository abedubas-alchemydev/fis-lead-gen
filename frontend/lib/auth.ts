import { betterAuth } from "better-auth";
import { APIError } from "better-auth/api";
import { genericOAuth } from "better-auth/plugins";
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

// Decodes a JWT payload without verifying the signature. Safe to use
// here because Better Auth has already verified the id_token against
// the provider's JWKS before storing it on the account row -- we just
// need to read the email claim out of the payload.
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return null;
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const json = Buffer.from(padded, "base64").toString("utf-8");
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

// Provider-specific email extraction. Google + Yahoo expose ``email``
// directly. Microsoft prefers ``preferred_username`` (it's the email
// on AAD work/school + outlook.com consumer); ``upn`` and ``email``
// are documented fallbacks for older tenants. Returns null if no
// known claim is present -- the lazy-backfill path in the FastAPI
// OAuth helper covers that case on first send.
function extractEmailFromIdToken(
  providerId: string,
  idToken: string | null | undefined
): string | null {
  if (!idToken) return null;
  const payload = decodeJwtPayload(idToken);
  if (!payload) return null;
  if (providerId === "google" || providerId === "yahoo") {
    return typeof payload.email === "string" ? payload.email : null;
  }
  if (providerId === "microsoft") {
    if (typeof payload.preferred_username === "string") return payload.preferred_username;
    if (typeof payload.upn === "string") return payload.upn;
    if (typeof payload.email === "string") return payload.email;
    return null;
  }
  return null;
}

type AccountHookRow = {
  id: string;
  providerId?: string;
  provider_id?: string;
  idToken?: string | null;
  id_token?: string | null;
  emailAddress?: string | null;
  email_address?: string | null;
};

async function captureAccountEmail(account: AccountHookRow): Promise<void> {
  try {
    if (account.emailAddress || account.email_address) return;
    const providerId = account.providerId ?? account.provider_id ?? "";
    const idToken = account.idToken ?? account.id_token ?? null;
    const email = extractEmailFromIdToken(providerId, idToken);
    if (!email) return;
    await database.query(
      'UPDATE "account" SET email_address = $1 WHERE id = $2 AND email_address IS NULL',
      [email, account.id]
    );
  } catch (err) {
    // Never block link/refresh on email capture -- the FastAPI side
    // has a lazy backfill on first send that covers misses here.
    console.error("[ACCOUNT-EMAIL] Failed to capture email_address:", err);
  }
}

const extraTrustedOrigins = (process.env.BETTER_AUTH_TRUSTED_ORIGINS ?? "")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);

export const auth = betterAuth({
  database,
  baseURL: appUrl,
  basePath: "/api/auth",
  trustedOrigins: [
    process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000",
    "http://127.0.0.1:3000",
    ...extraTrustedOrigins
  ],
  emailAndPassword: {
    enabled: true,
    minPasswordLength: 8,
    maxPasswordLength: 128,
    sendResetPassword: async ({ user, url }) => {
      await sendPasswordResetEmail({ user, url });
    }
  },
  socialProviders: {
    // "Continue with Google" on /login + /signup. Default scopes
    // (openid + email + profile) — gmail.send is intentionally NOT
    // requested here. The per-contact Outreach modal triggers
    // authClient.linkSocial({ scopes: ["gmail.send"] }) the first time
    // the user clicks Send, so the login consent screen stays minimal
    // and users who never send outreach are never asked for the
    // restricted scope.
    //
    // accessType=offline + prompt=consent are required to reliably
    // receive a refresh_token. Google omits refresh_token on silent
    // re-auth, so without prompt=consent the second sign-in leaves the
    // account row without one and the backend can never refresh the
    // access token (see services/google_oauth.py).
    google: {
      clientId: process.env.GOOGLE_CLIENT_ID ?? "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET ?? "",
      accessType: "offline",
      prompt: "consent"
    },
    // "Continue with Outlook" — Microsoft work / school AD accounts AND
    // consumer Microsoft accounts (outlook.com, hotmail.com, live.com).
    // tenant ``common`` accepts both. ``offline_access`` + ``prompt: consent``
    // mirror the Google flow so the FE reliably receives a refresh_token
    // that the backend's microsoft_oauth.py can use to refresh the
    // bearer token before each MS Graph send. The Mail.Send scope stays
    // out of the initial consent — the Outreach modal's linkSocial flow
    // requests it the first time the user clicks Send, matching the
    // Gmail incremental-consent pattern.
    microsoft: {
      clientId: process.env.MICROSOFT_CLIENT_ID ?? "",
      clientSecret: process.env.MICROSOFT_CLIENT_SECRET ?? "",
      scope: ["openid", "email", "profile", "offline_access"],
      prompt: "consent"
    }
  },
  // Yahoo isn't a Better Auth first-party social provider in 1.3.6, so
  // it's wired via the genericOAuth plugin against Yahoo's OIDC
  // discovery URL. The backend's yahoo_oauth.py refresh path is the same
  // shape as the Google / Microsoft helpers; the only Yahoo-specific
  // wrinkle is that the SEND path is SMTP+XOAUTH2 (no REST send API).
  plugins: [
    genericOAuth({
      config: [
        {
          providerId: "yahoo",
          clientId: process.env.YAHOO_CLIENT_ID ?? "",
          clientSecret: process.env.YAHOO_CLIENT_SECRET ?? "",
          discoveryUrl:
            "https://api.login.yahoo.com/.well-known/openid-configuration",
          scopes: ["openid", "email", "profile"],
          // mail-w is the send scope — requested on demand from the
          // outreach modal via authClient.linkSocial, same as the
          // gmail.send incremental-consent flow.
          prompt: "consent"
        }
      ]
    })
  ],
  emailVerification: {
    sendOnSignUp: true,
    // Off on purpose: session.create.before blocks non-active users, so an
    // auto-login attempt right after verify would throw APIError FORBIDDEN
    // and the user would see an ugly error instead of a clean "email
    // verified, waiting on admin approval" state. With this off, the verify
    // endpoint just flips email_verified and stops.
    autoSignInAfterVerification: false,
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
      },
      feature_permissions: {
        type: "string[]",
        required: false,
        defaultValue: [],
        input: false,
        returned: true
      }
    }
  },
  session: {
    // 7-day absolute TTL, refreshed on any request older than 24h. Matches
    // BetterAuth defaults. Keeps active users logged in indefinitely on a
    // rolling window and drops inactive ones after a week.
    expiresIn: 60 * 60 * 24 * 7,
    updateAge: 60 * 60 * 24,
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
    // Multi-sender outreach: a user can link a SECOND Google account
    // (or any provider combo). ``allowDifferentEmails`` is required —
    // without it, Better Auth aborts the link with ``email_doesn't_match``
    // when the new OAuth identity's email differs from the session
    // user's email (callback.mjs:106). ``trustedProviders`` lists the
    // providers that are allowed to auto-link to an existing account
    // without an extra confirmation step.
    accountLinking: {
      enabled: true,
      allowDifferentEmails: true,
      trustedProviders: ["google", "microsoft", "yahoo"]
    },
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
    account: {
      // Fires after a fresh OAuth link (Better Auth's
      // ``createAccount`` path in callback.mjs). Decodes the stored
      // id_token and writes ``email_address`` so the multi-sender
      // picker in the outreach modal can show "alice@personal.com"
      // / "alice@firm.com" instead of just "Google" / "Google".
      create: {
        after: async (account) => {
          await captureAccountEmail(account as AccountHookRow);
        }
      },
      // Fires after a token refresh or scope re-grant (Better Auth's
      // ``updateAccount``). Useful when the original create.after
      // missed (e.g. legacy rows linked before this hook existed)
      // and the user later re-granted send scope -- the id_token
      // gets refreshed, and we backfill the address opportunistically.
      update: {
        after: async (account) => {
          await captureAccountEmail(account as AccountHookRow);
        }
      }
    },
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
        },
        // Login event capture for the admin user-activity feed. Fires after
        // the session row commits — the only reliable "login completed"
        // signal that covers both email/password and OAuth paths. Cookie
        // cache renewals do not write a session row, so this only fires on
        // actual login. Errors here must never block login: wrap and log.
        after: async (session) => {
          try {
            await database.query(
              `INSERT INTO audit_log (user_id, action, details, timestamp)
               VALUES ($1, 'login', $2, NOW())`,
              [
                session.userId,
                JSON.stringify({
                  ip: session.ipAddress ?? null,
                  user_agent: session.userAgent ?? null,
                  session_id: session.id
                })
              ]
            );
          } catch (err) {
            console.error("[ACTIVITY] Failed to record login audit row:", err);
          }
        }
      },
      delete: {
        after: async (session) => {
          try {
            await database.query(
              `INSERT INTO audit_log (user_id, action, details, timestamp)
               VALUES ($1, 'logout', $2, NOW())`,
              [
                session.userId,
                JSON.stringify({
                  ip: session.ipAddress ?? null,
                  session_id: session.id
                })
              ]
            );
          } catch (err) {
            console.error("[ACTIVITY] Failed to record logout audit row:", err);
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
