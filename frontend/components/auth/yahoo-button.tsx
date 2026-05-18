"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { authClient } from "@/lib/auth-client";

/** "Continue with Yahoo" button for both /login and /signup.
 *
 * Yahoo isn't a Better Auth first-party social provider in 1.3.6, so
 * it's wired through the genericOAuth plugin (server-side in
 * ``lib/auth.ts``, client-side via ``genericOAuthClient`` in
 * ``lib/auth-client.ts``). The client API is ``authClient.signIn.oauth2``
 * with ``providerId: "yahoo"`` — different name, same semantics as
 * ``signIn.social``.
 *
 * Default scopes include ``openid email profile``; ``mail-w`` (Yahoo's
 * send scope, the SMTP XOAUTH2 backend uses it) is requested only on
 * the first Send click via ``authClient.linkSocial``.
 */
export function YahooButton({
  mode,
  enabled = true
}: {
  mode: "login" | "signup";
  enabled?: boolean;
}) {
  const [isPending, setIsPending] = useState(false);

  async function handleClick() {
    setIsPending(true);
    try {
      await authClient.signIn.oauth2({
        providerId: "yahoo",
        callbackURL: "/dashboard",
        errorCallbackURL: "/pending-approval"
      });
    } catch {
      setIsPending(false);
    }
  }

  if (!enabled) {
    return (
      <button
        type="button"
        disabled
        aria-disabled
        title="Yahoo sign-in is being set up. Available soon."
        className="flex w-full cursor-not-allowed items-center justify-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm font-medium text-[var(--text-muted,#94a3b8)] opacity-60 shadow-sm"
      >
        <YahooLogo />
        <span>
          {mode === "signup" ? "Sign up with Yahoo" : "Continue with Yahoo"}
        </span>
        <span className="ml-1 rounded-full bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
          Coming soon
        </span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isPending}
      className="flex w-full items-center justify-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm font-medium text-[var(--text,#0f172a)] shadow-sm transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
    >
      {isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      ) : (
        <YahooLogo />
      )}
      <span>
        {isPending
          ? "Redirecting to Yahoo..."
          : mode === "signup"
            ? "Sign up with Yahoo"
            : "Continue with Yahoo"}
      </span>
    </button>
  );
}

// Yahoo's wordmark in purple. Simple "Y!" glyph to keep the row height
// consistent with the Google / Microsoft buttons.
function YahooLogo() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <rect width="18" height="18" rx="3" fill="#6001D2" />
      <text
        x="9"
        y="13"
        textAnchor="middle"
        fontFamily="Arial, sans-serif"
        fontSize="12"
        fontWeight="700"
        fill="#FFFFFF"
      >
        Y!
      </text>
    </svg>
  );
}
