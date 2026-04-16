"use client";

import type { FormEvent } from "react";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import { authClient } from "@/lib/auth-client";

type AuthFormMode = "login" | "signup";

export function AuthForm({ mode }: { mode: AuthFormMode }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [signupSuccess, setSignupSuccess] = useState(false);
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
          setSignupSuccess(true);
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

  if (signupSuccess) {
    return (
      <div className="animate-scale-in space-y-5">
        <div className="flex flex-col items-center rounded-2xl bg-emerald-50 px-6 py-8 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-success/10">
            <CheckCircle2 className="h-6 w-6 text-success" />
          </div>
          <p className="mt-4 text-base font-semibold text-emerald-800">Account created</p>
          <p className="mt-2 max-w-xs text-sm leading-relaxed text-emerald-700">
            A verification email has been sent. Please check your inbox and verify
            your email to activate your account.
          </p>
        </div>
        <button
          onClick={() => router.push("/login")}
          className="w-full rounded-2xl bg-navy px-4 py-3.5 text-sm font-semibold text-white transition hover:bg-[#112b54]"
        >
          Continue to sign in
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {mode === "signup" ? (
        <Input name="name" label="Full name" placeholder="Morgan Patel" required autoComplete="name" />
      ) : null}

      <Input name="email" type="email" label="Email" placeholder="you@company.com" required autoComplete="email" />

      <div>
        <Input
          name="password"
          type="password"
          label="Password"
          placeholder={mode === "signup" ? "Minimum 8 characters" : "Enter your password"}
          required
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
        />
        {mode === "signup" ? (
          <p className="mt-1.5 text-xs text-slate-400">Must be at least 8 characters</p>
        ) : null}
      </div>

      {mode === "signup" ? (
        <Input
          name="confirmPassword"
          type="password"
          label="Confirm password"
          placeholder="Re-enter your password"
          required
          autoComplete="new-password"
        />
      ) : null}

      {error ? (
        <div className="animate-scale-in rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={isPending}
        className="relative w-full overflow-hidden rounded-2xl bg-navy px-4 py-3.5 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] hover:shadow-xl hover:shadow-navy/20 disabled:cursor-not-allowed disabled:opacity-60"
      >
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
      </button>

      {mode === "login" ? (
        <div className="text-center">
          <a
            href="/forgot-password"
            className="text-sm text-slate-500 transition hover:text-blue"
          >
            Forgot your password?
          </a>
        </div>
      ) : null}
    </form>
  );
}
