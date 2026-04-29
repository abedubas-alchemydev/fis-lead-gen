"use client";

import { BellOff } from "lucide-react";

// Empty state for the alerts list card when the active category +
// filter combination produces zero rows. Mirrors the
// EmptyItemsState shipped on /my-favorites earlier today (cli04) so the
// two adjacent "nothing here yet" surfaces feel visually consistent.
//
// No CTA: alerts populate automatically as new SEC filings arrive, so
// there's nothing for the user to do beyond waiting or relaxing
// filters.
export function EmptyAlertsState() {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <BellOff className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No alerts to review
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        We&apos;ll surface new SEC filings here as they appear.
      </p>
    </div>
  );
}
