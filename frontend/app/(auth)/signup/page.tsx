import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";

export default function SignupPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent,#6366f1)]">Provision Access</p>
        <h2 className="mt-3 text-2xl font-bold text-[var(--text,#0f172a)]">Create your account</h2>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">
          New accounts default to the Viewer role. Administrators can promote access later.
        </p>
      </div>
      <AuthForm mode="signup" />
      <p className="mt-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
        Already have an account?{" "}
        <Link href="/login" className="font-semibold text-[var(--accent,#6366f1)] transition hover:text-[var(--text,#0f172a)]">
          Sign in
        </Link>
      </p>
    </div>
  );
}
