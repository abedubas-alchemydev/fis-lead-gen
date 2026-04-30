"use client";

import { Loader2, Search } from "lucide-react";

// In-progress visual shown inside the "Discovered emails"
// SectionPanel while the scan is queued or running and no provider
// has reported back yet. Mirrors the medallion pattern shipped on
// /alerts, /visited-firms, /my-favorites, /export, and /dashboard so
// the in-progress surface feels visually consistent.
//
// The four providers (Hunter, Snov, in-house crawler, theHarvester)
// run in parallel and typically settle in 5-30 seconds. The detail
// page already polls the scan endpoint every 1.5s, so this card
// updates its progress copy as `processed_items` ticks.
export function ScanResultsLoading({
  processed,
  total,
}: {
  processed: number;
  total: number;
}) {
  const helper =
    total > 0
      ? `${processed} of ${total} provider${total === 1 ? "" : "s"} complete`
      : "Just started — first results land in a few seconds.";

  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <Search className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 inline-flex items-center gap-2 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        <Loader2 className="h-4 w-4 animate-spin text-[#6366f1]" strokeWidth={2} aria-hidden />
        Searching for emails…
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Hunter, Snov, the in-house crawler, and theHarvester are running
        in parallel. This usually takes 5-30 seconds.
      </p>
      <p className="mx-auto mt-3 text-[12px] tabular-nums text-[var(--text-muted,#94a3b8)]">
        {helper}
      </p>
    </div>
  );
}
