"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api";
import type { BrokerDealerListItem, BrokerDealerListResponse } from "@/lib/types";

// Mockup's avatar gradient palette — rotated per row by index.
const AVATAR_GRADIENTS = [
  "linear-gradient(135deg,#6366f1,#8b5cf6)",
  "linear-gradient(135deg,#10b981,#06b6d4)",
  "linear-gradient(135deg,#ec4899,#f59e0b)",
  "linear-gradient(135deg,#3b82f6,#8b5cf6)",
  "linear-gradient(135deg,#06b6d4,#10b981)"
];

function initialsFromName(name: string): string {
  const words = name
    .replace(/[,&.]/g, "")
    .split(/\s+/)
    .filter((w) => w.length > 1);
  if (words.length === 0) return "BD";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}

function scoreColor(score: number | null): string {
  if (score === null) return "#64748b";
  if (score >= 90) return "#dc2626";
  if (score >= 80) return "#d97706";
  if (score >= 70) return "#4f46e5";
  return "#059669";
}

export function TopLeadsCard() {
  const [items, setItems] = useState<BrokerDealerListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const response = await apiRequest<BrokerDealerListResponse>(
          "/api/v1/broker-dealers?lead_priority=hot&limit=5&sort=lead_score_desc"
        );
        if (!active) return;
        setItems(response.items);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Could not load leads");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  return (
    // `h-full` so this card sits at the row's stretched height, matching the
    // trend card on the left. Themed border/bg/shadow keep it visually
    // aligned with the KPI + trend cards and the mockup `.card` rule.
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Top high-value leads
          </h3>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            Ranked by weighted lead score
          </p>
        </div>
        {/* .link-btn: 12px, weight 600, color var(--accent)=#6366f1. */}
        <Link
          href="/master-list?lead_priority=hot"
          className="inline-flex items-center gap-1 text-[12px] font-semibold text-[#6366f1] transition hover:text-[#a5b4fc]"
        >
          View all
          <svg width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
            <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Link>
      </div>

      {error ? (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
      ) : loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 border-t border-slate-200/70 py-3 first:border-t-0">
              <div className="h-9 w-9 animate-pulse rounded-lg bg-slate-100" />
              <div className="flex-1">
                <div className="h-4 w-40 animate-pulse rounded bg-slate-100" />
                <div className="mt-1.5 h-3 w-32 animate-pulse rounded bg-slate-100" />
              </div>
              <div className="h-7 w-14 animate-pulse rounded bg-slate-100" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-500">
          No high-value leads yet. Check back after the next scoring pass.
        </div>
      ) : (
        <div>
          {items.map((item, idx) => {
            const color = scoreColor(item.lead_score);
            const pct = item.lead_score !== null ? Math.max(0, Math.min(100, item.lead_score)) : 0;
            const dashArray = `${(pct / 100) * 88} 88`;
            return (
              <Link
                key={item.id}
                href={`/master-list/${item.id}`}
                className="grid grid-cols-[36px_1fr_auto] items-center gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-3 transition first:border-t-0 hover:bg-[var(--surface-2,#f1f6fd)]"
              >
                <div
                  className="grid h-9 w-9 place-items-center rounded-[10px] text-[13px] font-bold text-white"
                  style={{ background: AVATAR_GRADIENTS[idx % AVATAR_GRADIENTS.length] }}
                >
                  {initialsFromName(item.name)}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
                    {item.name}
                  </div>
                  <div className="mt-0.5 truncate text-[11px] text-[var(--text-muted,#94a3b8)]">
                    {item.crd_number ? `CRD #${item.crd_number}` : "No CRD"}
                    {item.state ? ` · ${item.state}` : ""}
                    {item.is_deficient ? " · Deficient" : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[14px] font-bold" style={{ color }}>
                    {item.lead_score ?? "—"}
                  </span>
                  <svg width="30" height="30" viewBox="0 0 36 36" className="shrink-0">
                    <circle cx="18" cy="18" r="14" fill="none" stroke="rgba(15,23,42,0.06)" strokeWidth="3" />
                    <circle
                      cx="18"
                      cy="18"
                      r="14"
                      fill="none"
                      stroke={color}
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeDasharray={dashArray}
                      transform="rotate(-90 18 18)"
                    />
                  </svg>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
