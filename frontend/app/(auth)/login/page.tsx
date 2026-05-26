import { AuthForm } from "@/components/auth/auth-form";

// Force per-request server rendering so process.env is read from the
// Cloud Run runtime, not from the CI build environment. Without this
// Next.js statically pre-renders the page during `next build`, baking
// in whatever the CI runner sees for MICROSOFT_/YAHOO_CLIENT_ID
// (currently nothing) — which leaves the OAuth buttons stuck on
// "Coming soon" even after the secrets land on Cloud Run.
export const dynamic = "force-dynamic";

// Outlook + Yahoo OAuth buttons render disabled with a "Coming soon"
// hint when the corresponding credentials aren't configured yet. Reading
// server-side here (env vars are not NEXT_PUBLIC_*) and passing flags
// down means once the secrets land in Cloud Run, the buttons light up
// without any code change.
const microsoftEnabled = Boolean(
  process.env.MICROSOFT_CLIENT_ID && process.env.MICROSOFT_CLIENT_SECRET
);
const yahooEnabled = Boolean(
  process.env.YAHOO_CLIENT_ID && process.env.YAHOO_CLIENT_SECRET
);

export default function LoginPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent,#6366f1)]">Welcome back</p>
        <h2 className="mt-3 text-3xl font-bold leading-tight text-[var(--text,#0f172a)]">Sign in to your workspace.</h2>
        <p className="mt-3 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">
          Resume your research across the clearing layer.
        </p>
      </div>
      <AuthForm
        mode="login"
        microsoftEnabled={microsoftEnabled}
        yahooEnabled={yahooEnabled}
      />
    </div>
  );
}
