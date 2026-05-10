import Link from "next/link";

import { AuthForm } from "@/components/auth/auth-form";

export default function LoginPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Secure Access</p>
        <h2 className="mt-3 text-2xl font-bold text-navy">Welcome back</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          Sign in with your credentials to access the intelligence workspace.
        </p>
      </div>
      <AuthForm mode="login" />
      <p className="mt-8 text-center text-sm text-slate-500">
        Need an account?{" "}
        <Link href="/signup" className="font-semibold text-blue transition hover:text-navy">
          Create one
        </Link>
      </p>
    </div>
  );
}
