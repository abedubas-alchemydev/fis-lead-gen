"use client";

import { FileText } from "lucide-react";

// Empty state shown inside the Preview SectionPanel on /export when
// the active filter combination yields zero matching firms. Mirrors
// the EmptyAlertsState / EmptyItemsState pill so the three "nothing
// here yet" surfaces feel visually consistent.
//
// No CTA: the fix is to relax the filters, which the user already
// sees right above this panel. Copy reinforces the PRD-locked
// contract (≤100 rows, watermark, 3/day cap) so users know what
// they're signing up for once their filters do match.
export function EmptyExportMatchesState() {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <FileText className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No firms match these filters
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Adjust the filters above to pick up firms. Each export ships
        up to 100 rows, includes a source watermark, and counts
        toward your 3 exports/day cap.
      </p>
    </div>
  );
}
