import { createAuthClient } from "better-auth/react";
import { genericOAuthClient } from "better-auth/client/plugins";

function resolveAuthBaseUrl() {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }

  return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
}

export const authClient = createAuthClient({
  baseURL: resolveAuthBaseUrl(),
  // Mirrors the server-side genericOAuth plugin in lib/auth.ts so the
  // client exposes authClient.signIn.oauth2 / authClient.linkSocial for
  // the Yahoo provider (not a Better Auth first-party social provider
  // in 1.3.6, so it has to ride the generic OAuth plugin).
  plugins: [genericOAuthClient()]
});
