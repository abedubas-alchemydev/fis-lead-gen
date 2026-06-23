"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { BrokerDealerListItem, BrokerDealerListResponse } from "@/lib/types";

interface NewBdsModalProps {
  onClose: () => void;
}

function isoDateNDaysAgo(days: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

export function NewBdsModal({ onClose }: NewBdsModalProps) {
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

    const since = isoDateNDaysAgo(90);
    apiRequest<BrokerDealerListResponse>(
      `/api/v1/broker-dealers?registered_after=${since}&list=all&sort_by=registration_date&sort_dir=desc&limit=100`
    )
      .then((resp) => {
        if (!active) return;
        setItems(resp.items);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Could not load new broker-dealers.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-bds-modal-title"
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
              New BDs · 90 days
            </p>
            <h2
              id="new-bds-modal-title"
              className="mt-1 text-lg font-semibold tracking-tight text-[var(--text,#0f172a)]"
            >
              Recent registrations
            </h2>
            <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
              Broker-dealers registered on or after {formatDate(isoDateNDaysAgo(90))}.
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
              No new broker-dealers registered in the last 90 days.
            </div>
          ) : (
            <ul className="space-y-2">
              {items.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/master-list/${item.id}`}
                    onClick={onClose}
                    className="flex items-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-3 transition hover:border-violet-300 hover:bg-violet-50/40"
                  >
                    <div
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-[12px] font-bold text-white"
                      style={{ background: "linear-gradient(135deg,#8b5cf6,#6366f1)" }}
                    >
                      {initialsFromName(item.name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
                        {item.name}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted,#94a3b8)]">
                        {item.crd_number ? `CRD #${item.crd_number}` : "No CRD"}
                        {item.state ? ` · ${item.state}` : ""}
                        {item.is_deficient ? " · Deficient" : ""}
                      </div>
                    </div>
                    <div className="text-right text-[11px] text-[var(--text-muted,#94a3b8)]">
                      <div className="font-medium text-[var(--text-dim,#475569)]">
                        {formatDate(item.registration_date)}
                      </div>
                      <div className="mt-0.5">Registered</div>
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
