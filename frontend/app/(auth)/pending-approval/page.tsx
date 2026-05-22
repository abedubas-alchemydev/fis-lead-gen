import Link from "next/link";
import { Clock, Mail } from "lucide-react";

// OAuth buttons append ?via=oauth so we can hide the "verify your email" card —
// Google/Microsoft/Yahoo have already verified the email at the IdP, so only
// admin approval is pending. Email/password signups land here without the
// query param and still see both cards.
export default function PendingApprovalPage({
  searchParams
}: {
  searchParams: { via?: string };
}) {
  const viaOAuth = searchParams.via === "oauth";

  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--accent,#6366f1)]">Account pending</p>
        <h2 className="mt-3 text-2xl font-bold text-[var(--text,#0f172a)]">Thanks for signing up</h2>
        <p className="mt-2 text-sm leading-relaxed text-[var(--text-muted,#94a3b8)]">
          Your account is being reviewed by our team. We&apos;ll follow up once it&apos;s approved.
        </p>
      </div>

      <div className="space-y-3">
        <div className="flex items-start gap-3 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm leading-6 text-[var(--text-dim,#475569)]">
          <Clock className="mt-0.5 h-4 w-4 flex-none text-[var(--accent,#6366f1)]" />
          <p>
            <span className="font-semibold text-[var(--text,#0f172a)]">Awaiting admin approval.</span> An
            administrator has been notified. You won&apos;t be able to sign in until
            approval is granted.
          </p>
        </div>

        {viaOAuth ? null : (
          <div className="flex items-start gap-3 rounded-2xl border border-amber-500/25 bg-amber-500/12 px-4 py-3 text-sm leading-6 text-amber-700">
            <Mail className="mt-0.5 h-4 w-4 flex-none text-warning" />
            <p>
              <span className="font-semibold">Verify your email too.</span> Click the
              verification link we sent to your inbox. Both email verification and admin
              approval are required to sign in.
            </p>
          </div>
        )}
      </div>

      <p className="mt-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
        Already approved?{" "}
        <Link href="/login" className="font-semibold text-[var(--accent,#6366f1)] transition hover:text-[var(--text,#0f172a)]">
          Sign in
        </Link>
      </p>
    </div>
  );
}
