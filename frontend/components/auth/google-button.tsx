"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { authClient } from "@/lib/auth-client";

/** "Continue with Google" button for both /login and /signup.
 *
 * Better Auth treats signIn.social as an upsert: an existing Google
 * account logs in, a new Google email creates a fresh user row. Either
 * way the configured ``databaseHooks`` fire — admin gets the approval
 * email on first signup; the session.create.before guard bounces
 * pending/rejected users to ``errorCallbackURL`` (we send them to
 * /pending-approval, which is the same destination email-password
 * signups land on).
 *
 * Scope-wise this requests the Better Auth defaults only (openid +
 * email + profile). The gmail.send scope is requested later, the first
 * time the user clicks Send in the Outreach modal, via
 * authClient.linkSocial — keeps the login consent screen lightweight
 * and avoids asking users who never send outreach for a restricted
 * scope.
 */
export function GoogleButton({ mode }: { mode: "login" | "signup" }) {
  const [isPending, setIsPending] = useState(false);

  async function handleClick() {
    setIsPending(true);
    try {
      await authClient.signIn.social({
        provider: "google",
        callbackURL: "/dashboard",
        errorCallbackURL: "/pending-approval"
      });
    } catch {
      // signIn.social does a full-page navigation, so reaching this
      // catch means the call failed before redirecting (network blip,
      // misconfigured client). Re-enable the button so the user can
      // try again.
      setIsPending(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isPending}
      className="flex w-full items-center justify-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-navy shadow-sm transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      ) : (
        <GoogleLogo />
      )}
      <span>
        {isPending
          ? "Redirecting to Google..."
          : mode === "signup"
            ? "Sign up with Google"
            : "Continue with Google"}
      </span>
    </button>
  );
}

function GoogleLogo() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M17.64 9.205c0-.639-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z"
        fill="#4285F4"
      />
      <path
        d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.836.86-3.048.86-2.344 0-4.328-1.583-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z"
        fill="#34A853"
      />
      <path
        d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z"
        fill="#FBBC05"
      />
      <path
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z"
        fill="#EA4335"
      />
    </svg>
  );
}
