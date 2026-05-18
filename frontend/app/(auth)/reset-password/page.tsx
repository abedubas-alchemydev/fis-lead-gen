import Link from "next/link";

import { ResetPasswordForm } from "@/components/auth/reset-password-form";

export default function ResetPasswordPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent,#6366f1)]">Account Recovery</p>
        <h2 className="mt-3 text-2xl font-bold text-[var(--text,#0f172a)]">Set a new password</h2>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">
          Choose a strong password with at least 8 characters to secure your account.
        </p>
      </div>
      <ResetPasswordForm />
      <p className="mt-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
        Back to{" "}
        <Link href="/login" className="font-semibold text-[var(--accent,#6366f1)] transition hover:text-[var(--text,#0f172a)]">
          Sign in
        </Link>
      </p>
    </div>
  );
}
