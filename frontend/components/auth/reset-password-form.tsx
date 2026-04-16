"use client";

import type { FormEvent } from "react";
import { Suspense, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function ResetPasswordFormInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isPending, startTransition] = useTransition();

  const token = searchParams.get("token");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const formData = new FormData(event.currentTarget);
    const newPassword = String(formData.get("password") ?? "");
    const confirmPassword = String(formData.get("confirmPassword") ?? "");

    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    if (!token) {
      setError("Invalid or missing reset token. Please request a new reset link.");
      return;
    }

    startTransition(async () => {
      try {
        const response = await fetch("/api/auth/reset-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ newPassword, token }),
          credentials: "include",
        });

        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.message ?? "Unable to reset password.");
        }

        setSuccess(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Unable to reset password.";
        setError(message);
      }
    });
  }

  if (!token) {
    return (
      <div className="rounded-2xl bg-amber-50 px-4 py-4 text-sm text-amber-700">
        <p className="font-medium">Invalid reset link</p>
        <p className="mt-2">This reset link is missing a token. Please request a new password reset from the login page.</p>
      </div>
    );
  }

  if (success) {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl bg-emerald-50 px-4 py-4 text-sm text-emerald-700">
          <p className="font-medium">Password updated</p>
          <p className="mt-2">Your password has been reset successfully. You can now sign in with your new password.</p>
        </div>
        <Button onClick={() => router.push("/login")} className="w-full">
          Go to sign in
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Input name="password" type="password" label="New password" placeholder="Minimum 8 characters" required />
      <Input name="confirmPassword" type="password" label="Confirm new password" placeholder="Re-enter your password" required />
      {error ? <p className="rounded-2xl bg-red-50 px-4 py-3 text-sm text-danger">{error}</p> : null}
      <Button type="submit" className="w-full" disabled={isPending}>
        {isPending ? "Resetting..." : "Reset password"}
      </Button>
    </form>
  );
}

export function ResetPasswordForm() {
  return (
    <Suspense fallback={<div className="h-40 animate-pulse rounded-2xl bg-slate-100" />}>
      <ResetPasswordFormInner />
    </Suspense>
  );
}
