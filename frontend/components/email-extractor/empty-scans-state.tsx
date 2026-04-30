"use client";

import { MailSearch } from "lucide-react";

// Empty state for the /email-extractor hub when the user has no
// scans on record. Mirrors the medallion pattern shipped on
// /alerts (EmptyAlertsState), /visited-firms (EmptyVisitedState),
// /my-favorites (EmptyItemsState), /export (EmptyExportMatchesState),
// and /dashboard (EmptyTopLeadsState) so the hub feels visually
// consistent with the rest of the app.
//
// No CTA — the new-scan form lives directly above this panel.
export function EmptyScansState() {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <MailSearch className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No scans yet
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Submit a domain above and we&apos;ll fan out to Hunter, Snov, the
        in-house crawler, and theHarvester. Past scans land here so you
        don&apos;t have to re-run them.
      </p>
    </div>
  );
}
