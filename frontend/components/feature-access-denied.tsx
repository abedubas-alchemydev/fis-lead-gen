import { ShieldAlert } from "lucide-react";

import { FEATURE_LABELS, type FeatureKey } from "@/lib/feature-permissions";

export function FeatureAccessDenied({ feature }: { feature: FeatureKey }) {
  const label = FEATURE_LABELS[feature];
  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      <section className="rounded-2xl border border-amber-500/25 bg-amber-500/12 p-8 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
        <div className="flex items-start gap-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface,#ffffff)] text-amber-600">
            <ShieldAlert className="h-5 w-5" aria-hidden />
          </div>
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-amber-700">Access Restricted</p>
            <h1 className="text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
              {label} is not available on your account
            </h1>
            <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
              Ask an administrator to grant you access to this feature from the user settings page.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
