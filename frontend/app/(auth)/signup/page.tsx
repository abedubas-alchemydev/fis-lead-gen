import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";

export default function SignupPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Provision Access</p>
        <h2 className="mt-3 text-2xl font-bold text-navy">Create your account</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          New accounts default to the Viewer role. Administrators can promote access later.
        </p>
      </div>
      <AuthForm mode="signup" />
      <p className="mt-8 text-center text-sm text-slate-500">
        Already have an account?{" "}
        <Link href="/login" className="font-semibold text-blue transition hover:text-navy">
          Sign in
        </Link>
      </p>
    </div>
  );
}
