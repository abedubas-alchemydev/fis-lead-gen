import Link from "next/link";
import { Clock, Mail } from "lucide-react";

export default function PendingApprovalPage() {
  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-[0.28em] text-blue">Account pending</p>
        <h2 className="mt-3 text-2xl font-bold text-navy">Thanks for signing up</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-500">
          Your account is being reviewed by our team. We&apos;ll follow up once it&apos;s approved.
        </p>
      </div>

      <div className="space-y-3">
        <div className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-700">
          <Clock className="mt-0.5 h-4 w-4 flex-none text-blue" />
          <p>
            <span className="font-semibold text-navy">Awaiting admin approval.</span> An
            administrator has been notified. You won&apos;t be able to sign in until
            approval is granted.
          </p>
        </div>

        <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
          <Mail className="mt-0.5 h-4 w-4 flex-none text-warning" />
          <p>
            <span className="font-semibold">Verify your email too.</span> Click the
            verification link we sent to your inbox. Both email verification and admin
            approval are required to sign in.
          </p>
        </div>
      </div>

      <p className="mt-8 text-center text-sm text-slate-500">
        Already approved?{" "}
        <Link href="/login" className="font-semibold text-blue transition hover:text-navy">
          Sign in
        </Link>
      </p>
    </div>
  );
}
