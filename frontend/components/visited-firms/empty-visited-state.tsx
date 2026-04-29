"use client";

import Link from "next/link";
import { ArrowRight, Eye } from "lucide-react";

// Empty state for /visited-firms when the user has no visit history yet.
// Mirrors the gradient-pill CTA in EmptyItemsState (/my-favorites) and the
// medallion shape in EmptyAlertsState (/alerts) so the three "nothing here
// yet" surfaces feel visually consistent.
export function EmptyVisitedState() {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <Eye className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No firms visited yet
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Open any firm from the master list to start your visit history.
      </p>
      <Link
        href="/master-list"
        className="mt-5 inline-flex items-center gap-2 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110"
      >
        Browse the master list
        <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
      </Link>
    </div>
  );
}
