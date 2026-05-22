"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { authClient } from "@/lib/auth-client";

/** "Continue with Outlook" button for both /login and /signup.
 *
 * Better Auth's ``microsoft`` social provider hits tenant ``common`` so
 * the same flow accepts work / school AD accounts AND consumer
 * outlook.com / hotmail.com / live.com accounts. ``databaseHooks.user
 * .create.after`` still fires for first signups (admin gets the
 * approval email); ``session.create.before`` still bounces pending /
 * rejected users to ``errorCallbackURL``.
 *
 * Scope-wise this requests the Better Auth defaults only (openid +
 * email + profile + offline_access). ``Mail.Send`` is requested later
 * via ``authClient.linkSocial`` the first time the user clicks Send in
 * the Outreach modal — mirrors the Gmail incremental-consent flow so
 * users who never send outreach aren't asked for the restricted scope.
 */
export function MicrosoftButton({
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
      await authClient.signIn.social({
        provider: "microsoft",
        callbackURL: "/dashboard",
        errorCallbackURL: "/pending-approval?via=oauth"
      });
    } catch {
      setIsPending(false);
    }
  }

  if (!enabled) {
    // Outlook OAuth code is shipped but the Azure AD app registration
    // hasn't landed yet (MICROSOFT_CLIENT_ID/SECRET unset). Render the
    // button disabled with a "Coming soon" hint so users know it's on
    // the way without getting an empty-client_id redirect to Microsoft.
    return (
      <button
        type="button"
        disabled
        aria-disabled
        title="Outlook sign-in is being set up. Available soon."
        className="flex w-full cursor-not-allowed items-center justify-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm font-medium text-[var(--text-muted,#94a3b8)] opacity-60 shadow-sm"
      >
        <MicrosoftLogo />
        <span>
          {mode === "signup" ? "Sign up with Outlook" : "Continue with Outlook"}
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
        <MicrosoftLogo />
      )}
      <span>
        {isPending
          ? "Redirecting to Microsoft..."
          : mode === "signup"
            ? "Sign up with Outlook"
            : "Continue with Outlook"}
      </span>
    </button>
  );
}

// Microsoft's official "four-square" logo (red / green / blue / yellow).
function MicrosoftLogo() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path fill="#F25022" d="M1 1h8v8H1z" />
      <path fill="#7FBA00" d="M9 1h8v8H9z" />
      <path fill="#00A4EF" d="M1 9h8v8H1z" />
      <path fill="#FFB900" d="M9 9h8v8H9z" />
    </svg>
  );
}
