"use client";

import type { FormEvent } from "react";
import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { PasswordInput } from "@/components/ui/password-input";
import { GoogleButton } from "@/components/auth/google-button";
import { MicrosoftButton } from "@/components/auth/microsoft-button";
import { YahooButton } from "@/components/auth/yahoo-button";
import { authClient } from "@/lib/auth-client";

type AuthFormMode = "login" | "signup";

export function AuthForm({ mode }: { mode: AuthFormMode }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const formData = new FormData(event.currentTarget);
    const email = String(formData.get("email") ?? "").trim();
    const password = String(formData.get("password") ?? "");
    const name = String(formData.get("name") ?? "").trim();

    setError(null);

    if (mode === "signup") {
      if (!name) {
        setError("Please enter your full name.");
        return;
      }
      if (password.length < 8) {
        setError("Password must be at least 8 characters.");
        return;
      }
      const confirmPassword = String(formData.get("confirmPassword") ?? "");
      if (password !== confirmPassword) {
        setError("Passwords do not match.");
        return;
      }
    }

    startTransition(async () => {
      try {
        if (mode === "signup") {
          const result = await authClient.signUp.email({ name, email, password });
          if (result.error) throw new Error(result.error.message);
          router.push("/pending-approval");
          router.refresh();
          return;
        }

        const result = await authClient.signIn.email({ email, password });
        if (result.error) throw new Error(result.error.message);
        router.push("/dashboard");
        router.refresh();
      } catch (submissionError) {
        setError(submissionError instanceof Error ? submissionError.message : "Unable to continue.");
      }
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <div className="space-y-2">
        <GoogleButton mode={mode} />
        <MicrosoftButton mode={mode} />
        <YahooButton mode={mode} />
      </div>

      <div className="relative" aria-hidden>
        <div className="absolute inset-x-0 top-1/2 h-px bg-[var(--border,rgba(30,64,175,0.1))]" />
        <div className="relative mx-auto w-fit bg-[var(--bg,#eaf3ff)] px-3 text-xs uppercase tracking-[0.2em] text-[var(--text-muted,#94a3b8)]">
          or continue with email
        </div>
      </div>

      {mode === "signup" ? (
        <Input name="name" label="Full name" placeholder="Morgan Patel" required autoComplete="name" />
      ) : null}

      <Input name="email" type="email" label="Email" placeholder="you@company.com" required autoComplete="email" />

      <div>
        <PasswordInput
          name="password"
          label="Password"
          placeholder={mode === "signup" ? "Minimum 8 characters" : "Enter your password"}
          required
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
        />
        {mode === "signup" ? (
          <p className="mt-1.5 text-xs text-[var(--text-muted,#94a3b8)]">Must be at least 8 characters</p>
        ) : null}
      </div>

      {mode === "signup" ? (
        <PasswordInput
          name="confirmPassword"
          label="Confirm password"
          placeholder="Re-enter your password"
          required
          autoComplete="new-password"
        />
      ) : null}

      {mode === "login" ? (
        // rememberMe is visual-only — better-auth 1.3.6 signIn.email() does not accept this flag.
        // Session lifetime is controlled server-side via betterAuth({ session: { expiresIn } }).
        <div className="flex items-center justify-between text-sm">
          <label className="inline-flex cursor-pointer items-center gap-2 text-[var(--text-dim,#475569)]">
            <input
              type="checkbox"
              name="rememberMe"
              className="h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20"
            />
            Remember me
          </label>
          <Link href="/forgot-password" className="font-medium text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--accent,#6366f1)]">
            Forgot password?
          </Link>
        </div>
      ) : null}

      {error ? (
        <div className="animate-scale-in rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      <Button type="submit" disabled={isPending} className="group relative w-full overflow-hidden">
        {isPending ? (
          <span className="flex items-center justify-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            {mode === "signup" ? "Creating account..." : "Signing in..."}
          </span>
        ) : mode === "signup" ? (
          "Create account"
        ) : (
          "Sign in"
        )}
        <span
          aria-hidden
          className="animate-shimmer pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        />
      </Button>

      {mode === "login" ? (
        <>
          <div className="relative pt-2">
            <div className="absolute inset-x-0 top-1/2 h-px bg-[var(--border,rgba(30,64,175,0.1))]" aria-hidden />
            <div className="relative mx-auto w-fit bg-[var(--bg,#eaf3ff)] px-3 text-xs uppercase tracking-[0.2em] text-[var(--text-muted,#94a3b8)]">
              Need access?
            </div>
          </div>
          <Link
            href="/signup"
            className="block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-center text-sm font-medium text-[var(--text,#0f172a)] transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)]"
          >
            Request an invite
          </Link>
        </>
      ) : null}
    </form>
  );
}
