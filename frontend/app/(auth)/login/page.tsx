import { AuthForm } from "@/components/auth/auth-form";

export default function LoginPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Welcome back</p>
        <h2 className="mt-3 text-3xl font-bold leading-tight text-navy">Sign in to your workspace.</h2>
        <p className="mt-3 text-sm leading-relaxed text-slate-500">
          Resume your research across the clearing layer.
        </p>
      </div>
      <AuthForm mode="login" />
    </div>
  );
}
