import Link from "next/link";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export default function ForgotPasswordPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent,#6366f1)]">Account Recovery</p>
        <h2 className="mt-3 text-2xl font-bold text-[var(--text,#0f172a)]">Reset your password</h2>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">
          Enter the email associated with your account and we will send a secure reset link.
        </p>
      </div>
      <ForgotPasswordForm />
      <p className="mt-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
        Remember your password?{" "}
        <Link href="/login" className="font-semibold text-[var(--accent,#6366f1)] transition hover:text-[var(--text,#0f172a)]">
          Sign in
        </Link>
      </p>
    </div>
  );
}
