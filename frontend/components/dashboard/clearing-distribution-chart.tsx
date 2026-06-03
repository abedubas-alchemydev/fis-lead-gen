"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { DashboardErrorCard } from "@/components/dashboard/dashboard-error-card";
import type { ClearingProviderShare } from "@/lib/types";

const PAGE_SIZE = 10;

// Pagination helper — same shape as the one in master-list-workspace-client.
type PageToken = number | "…";
function paginationPages(current: number, total: number): PageToken[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages: PageToken[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) pages.push("…");
  for (let i = start; i <= end; i++) pages.push(i);
  if (end < total - 1) pages.push("…");
  pages.push(total);
  return pages;
}

// Mockup palette — each row gets a stable color pair by position.
const ROW_PALETTES = [
  { swatch: "#1e3a8a", fillA: "#1e3a8a", fillB: "#3b82f6" },
  { swatch: "#ef4444", fillA: "#b91c1c", fillB: "#ef4444" },
  { swatch: "#9ca3af", fillA: "#6b7280", fillB: "#9ca3af" },
  { swatch: "#fbbf24", fillA: "#d97706", fillB: "#fbbf24" },
  { swatch: "#ec4899", fillA: "#be185d", fillB: "#ec4899" },
  { swatch: "#10b981", fillA: "#047857", fillB: "#10b981" },
  { swatch: "#06b6d4", fillA: "#0e7490", fillB: "#06b6d4" },
  { swatch: "#8b5cf6", fillA: "#6d28d9", fillB: "#8b5cf6" },
] as const;

// Real extraction data produces compound provider names like
// "Goldman, Sachs & Co., Pershing LLC, Mirae Asset Securities (USA), Inc."
// that wrap and destroy row rhythm. Collapse to single-line + ellipsis and
// expose the full text via the title attr so it's still inspectable.
function formatFirmsLabel(count: number): string {
  return `${count.toLocaleString()} ${count === 1 ? "firm" : "firms"}`;
}

// "0%" stays "0%". Real values below 0.1% render as "<0.1%" so we don't
// lie with "0.0%" when 1-of-3000 is technically 0.033%.
function formatPercentLabel(percentage: number): string {
  if (percentage <= 0) return "0%";
  if (percentage < 0.1) return "<0.1%";
  return `${percentage.toFixed(1)}%`;
}

export function ClearingDistributionChart({
  items,
  loading = false,
  error = null,
  onRetry,
}: {
  items: ClearingProviderShare[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}) {
  const router = useRouter();
  const [page, setPage] = useState(1);

  // Header shared across loading/error/empty/data branches so the card
  // chrome (border, shadow, padding, title) stays stable while the
  // body swaps between states.
  const cardOuter =
    "flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5";
  const cardShadow = {
    boxShadow:
      "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
  } as const;

  if (loading) {
    return (
      <div className={cardOuter} style={cardShadow}>
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Clearing market &mdash; provider distribution
            </h3>
            <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
              Click a row to filter Broker Dealers
            </p>
          </div>
        </div>
        <div aria-busy>
          {Array.from({ length: 5 }).map((_, idx) => (
            <div
              key={`distribution-skel-${idx}`}
              className="grid w-full grid-cols-[10px_minmax(0,40%)_minmax(80px,1fr)_56px] items-center gap-3.5 border-t border-[var(--border,rgba(30,64,175,0.1))] py-2.5 first:border-t-0"
            >
              <span className="h-2.5 w-2.5 animate-pulse rounded-[3px] bg-[var(--surface-2,#f1f6fd)]" />
              <div className="flex min-w-0 items-center gap-2">
                <span className="h-3 w-32 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <span className="h-3 w-12 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
              <div className="h-1.5 w-full animate-pulse rounded-full bg-[var(--surface-2,#f1f6fd)]" />
              <span className="h-3 w-10 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)] justify-self-end" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={cardOuter} style={cardShadow}>
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Clearing market &mdash; provider distribution
            </h3>
            <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
              Click a row to filter Broker Dealers
            </p>
          </div>
        </div>
        <DashboardErrorCard
          title="Couldn&rsquo;t load clearing distribution"
          message={error}
          onRetry={onRetry ?? (() => undefined)}
        />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className={cardOuter} style={cardShadow}>
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Clearing Market
        </p>
        <p className="mt-3 text-sm text-[var(--text-muted,#94a3b8)]">
          Clearing distribution will appear as extracted provider data becomes available.
        </p>
      </div>
    );
  }

  // Normalize bar widths so the top bar reads at ~92% and everything else
  // scales against the leader — matches the mockup's visual rhythm and
  // keeps the track visible when percentages are near-zero in aggregate.
  // Derived from the full items list (not the current page) so a small
  // 0.3% bar on page 2 doesn't get rescaled into looking like the leader.
  const maxPercent = Math.max(...items.map((i) => i.percentage));
  const scale = (p: number) => (maxPercent > 0 ? (p / maxPercent) * 92 : 0);

  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  // Clamp on render so a shrinking items list (e.g., new fetch returns
  // fewer providers) never strands us on an empty page.
  const safePage = Math.min(Math.max(1, page), totalPages);
  const pageItems = items.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const pages = paginationPages(safePage, totalPages);

  return (
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Clearing market — provider distribution
          </h3>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            Click a row to filter Broker Dealers
          </p>
        </div>
      </div>

      <div className="flex-1">
        {pageItems.map((item, index) => {
          // Use the global index for color so each provider keeps its swatch
          // across pages (Pershing LLC stays blue whether it's row 1 or row 11).
          const globalIndex = (safePage - 1) * PAGE_SIZE + index;
          const palette = ROW_PALETTES[globalIndex % ROW_PALETTES.length];
          const firmsLabel = formatFirmsLabel(item.count);
          const percentLabel = formatPercentLabel(item.percentage);
          return (
            <button
              key={item.provider}
              type="button"
              onClick={() =>
                router.push(`/master-list?clearing_partner=${encodeURIComponent(item.provider)}`)
              }
              title={item.provider}
              // Grid columns keep the bar track and percentage aligned
              // across rows regardless of provider-name length:
              //   [swatch] [label cluster, capped at 40%] [bar, flexes]
              //   [percent, fixed 56px right-aligned]
              className="grid w-full grid-cols-[10px_minmax(0,40%)_minmax(80px,1fr)_56px] items-center gap-3.5 border-t border-[var(--border,rgba(30,64,175,0.1))] py-2.5 text-left transition first:border-t-0 hover:bg-[var(--surface-2,#f1f6fd)]"
            >
              <span
                className="h-2.5 w-2.5 rounded-[3px]"
                style={{ backgroundColor: palette.swatch }}
              />
              <div className="flex min-w-0 items-center gap-2">
                <span className="truncate text-[13px] font-medium text-[var(--text,#0f172a)]">
                  {item.provider}
                </span>
                <span className="shrink-0 whitespace-nowrap text-[11px] text-[var(--text-muted,#94a3b8)]">
                  · {firmsLabel}
                </span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-[var(--surface-2,#f1f6fd)]">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${scale(item.percentage)}%`,
                    background: `linear-gradient(90deg, ${palette.fillA}, ${palette.fillB})`,
                  }}
                />
              </div>
              <span className="text-right text-[13px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
                {percentLabel}
              </span>
            </button>
          );
        })}
      </div>

      {totalPages > 1 ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] pt-3">
          <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            Showing {(safePage - 1) * PAGE_SIZE + 1}–
            {Math.min(safePage * PAGE_SIZE, items.length)} of {items.length}
          </p>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={safePage <= 1}
              onClick={() => setPage(Math.max(1, safePage - 1))}
              className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Previous
            </button>
            {pages.map((token, idx) =>
              token === "…" ? (
                <span
                  key={`ellipsis-${idx}`}
                  className="px-2 py-1.5 text-[12px] text-[var(--text-muted,#94a3b8)]"
                >
                  …
                </span>
              ) : (
                <button
                  key={token}
                  type="button"
                  onClick={() => setPage(token)}
                  aria-current={safePage === token ? "page" : undefined}
                  className={`min-w-[36px] rounded-[8px] border px-3 py-1.5 text-[12px] font-medium transition ${
                    safePage === token
                      ? "border-transparent bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                      : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)]"
                  }`}
                >
                  {token}
                </button>
              ),
            )}
            <button
              type="button"
              disabled={safePage >= totalPages}
              onClick={() => setPage(Math.min(totalPages, safePage + 1))}
              className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
