import Link from "next/link";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export default function ForgotPasswordPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Account Recovery</p>
        <h2 className="mt-3 text-2xl font-bold text-navy">Reset your password</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          Enter the email associated with your account and we will send a secure reset link.
        </p>
      </div>
      <ForgotPasswordForm />
      <p className="mt-8 text-center text-sm text-slate-500">
        Remember your password?{" "}
        <Link href="/login" className="font-semibold text-blue transition hover:text-navy">
          Sign in
        </Link>
      </p>
    </div>
  );
}
