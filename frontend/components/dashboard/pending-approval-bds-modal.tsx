"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiRequest, buildApiPath } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import type { BrokerDealerListItem, BrokerDealerListResponse } from "@/lib/types";

// Structural clone of new-bds-modal.tsx for the "Pending Approval BDs" KPI:
// broker-dealers that have FILED (a CRD is assigned at application and the
// nightly registration refresh has checked them) but show no SEC approval
// date yet. The BE's pending_approval=true mode retargets the
// registered_after window to the filed-date proxy
// COALESCE(formation_date, created_at::date) — every pending row's
// registration_date is NULL by definition — which also keeps this list
// disjoint from the New BD card (NULL never satisfies its cutoff).

interface PendingApprovalBdsModalProps {
  onClose: () => void;
}

type WindowDays = "30" | "90";

// Same 30/90-day-style windows as the New BD card. Default 90 so the modal
// list matches the KPI value (stats.pending_approval_bds is a 90-day count).
const WINDOW_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

function isoDateNDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

// Filed-date display: mirror the BE's window proxy so the date shown next
// to each row is the same one the 30/90-day filter matched on.
function filedDate(item: BrokerDealerListItem): string | null {
  return item.formation_date ?? item.created_at ?? null;
}

export function PendingApprovalBdsModal({ onClose }: PendingApprovalBdsModalProps) {
  const [windowDays, setWindowDays] = useState<WindowDays>("90");
  const [items, setItems] = useState<BrokerDealerListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    const since = isoDateNDaysAgo(Number(windowDays));
    apiRequest<BrokerDealerListResponse>(
      buildApiPath("/api/v1/broker-dealers", {
        pending_approval: true,
        registered_after: since,
        list: "all",
        sort_by: "name",
        sort_dir: "asc",
        limit: 100,
      })
    )
      .then((resp) => {
        if (!active) return;
        setItems(resp.items);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error ? err.message : "Could not load pending-approval broker-dealers."
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [windowDays]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pending-approval-bds-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={onClose}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative flex max-h-[80vh] w-full max-w-[640px] flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <div className="flex items-start justify-between gap-4 border-b border-[var(--border,rgba(30,64,175,0.1))] px-6 py-5">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
              Pending Approval BDs · {windowDays} days
            </p>
            <h2
              id="pending-approval-bds-modal-title"
              className="mt-1 text-lg font-semibold tracking-tight text-[var(--text,#0f172a)]"
            >
              Awaiting SEC approval
            </h2>
            <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
              Broker-dealers that filed (CRD assigned) on or after{" "}
              {formatDate(isoDateNDaysAgo(Number(windowDays)))} with no approval
              date yet. Open a firm for contacts and CI details.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-3,#dbeafe)] hover:text-[var(--text-dim,#475569)]"
            aria-label="Close"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Window toggle — same Segmented control as the workspace filter
            rows, pinned under the header so switching keeps scroll position. */}
        <div className="border-b border-[var(--border,rgba(30,64,175,0.1))] px-6 py-3">
          <Segmented
            value={windowDays}
            onChange={(next) => setWindowDays(next as WindowDays)}
            items={WINDOW_ITEMS}
            ariaLabel="Filed-date window"
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div aria-busy className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-3"
                >
                  <div className="h-9 w-9 animate-pulse rounded-lg bg-[var(--surface-3,#dbeafe)]" />
                  <div className="flex-1">
                    <div className="h-4 w-48 animate-pulse rounded bg-[var(--surface-3,#dbeafe)]" />
                    <div className="mt-2 h-3 w-32 animate-pulse rounded bg-[var(--surface-3,#dbeafe)]" />
                  </div>
                </div>
              ))}
            </div>
          ) : error ? (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          ) : items.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
              No broker-dealers awaiting approval filed in the last {windowDays}{" "}
              days.
            </div>
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/master-list/${item.id}`}
                    onClick={onClose}
                    className="flex items-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-3 transition hover:border-amber-300 hover:bg-amber-50/40"
                  >
                    <div
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-[12px] font-bold text-white"
                      style={{ background: "linear-gradient(135deg,#f59e0b,#ef4444)" }}
                    >
                      {initialsFromName(item.name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
                        {item.name}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted,#94a3b8)]">
                        {item.crd_number ? `CRD #${item.crd_number}` : "No CRD"}
                        {item.status ? ` · ${item.status}` : ""}
                        {item.state ? ` · ${item.state}` : ""}
                      </div>
                    </div>
                    <div className="text-right text-[11px] text-[var(--text-muted,#94a3b8)]">
                      <div className="font-medium text-[var(--text-dim,#475569)]">
                        {formatDate(filedDate(item))}
                      </div>
                      <div className="mt-0.5">Filed</div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function initialsFromName(name: string): string {
  const words = name
    .replace(/[,&.]/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 1);
  if (words.length === 0) return "BD";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}
