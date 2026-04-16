import Link from "next/link";

import { ResetPasswordForm } from "@/components/auth/reset-password-form";

export default function ResetPasswordPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Account Recovery</p>
        <h2 className="mt-3 text-2xl font-bold text-navy">Set a new password</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          Choose a strong password with at least 8 characters to secure your account.
        </p>
      </div>
      <ResetPasswordForm />
      <p className="mt-8 text-center text-sm text-slate-500">
        Back to{" "}
        <Link href="/login" className="font-semibold text-blue transition hover:text-navy">
          Sign in
        </Link>
      </p>
    </div>
  );
}
