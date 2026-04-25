"use client";

import Link from "next/link";
import { Clock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Pill, type PillVariant } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { listVisits, type VisitListItem } from "@/lib/favorites";
import { formatCurrency, formatPercent, formatRelativeTime } from "@/lib/format";

const PAGE_SIZE = 25;

function formatLocation(city: string | null, state: string | null): string {
  const parts = [city, state].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(", ") : "Location not on file";
}

function formatVisitCount(count: number): string {
  return `${count.toLocaleString()} visit${count === 1 ? "" : "s"}`;
}

// Local backend-enum → Pill variant / label mappings — cloned from the
// master-list table renderer so we don't fork a new shared util in this
// PR. The shared HealthBadge / LeadPriorityBadge components are still
// used by /my-favorites and other surfaces; we render <Pill> inline
// here to match the language used on /master-list and /master-list/{id}.
function healthVariant(status: string | null): PillVariant {
  if (status === "healthy") return "healthy";
  if (status === "ok") return "ok";
  if (status === "at_risk") return "risk";
  return "unknown";
}

function healthLabel(status: string | null): string {
  if (status === "healthy") return "Healthy";
  if (status === "ok") return "OK";
  if (status === "at_risk") return "At Risk";
  return "Unknown";
}

function priorityVariant(priority: string | null): PillVariant | null {
  if (priority === "hot") return "hot";
  if (priority === "warm") return "warm";
  if (priority === "cold") return "cold";
  return null;
}

function priorityLabel(priority: string | null): string {
  if (priority === "hot") return "Hot";
  if (priority === "warm") return "Warm";
  if (priority === "cold") return "Cold";
  return "Unscored";
}

export function VisitedFirmsClient() {
  const [items, setItems] = useState<VisitListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    listVisits({ limit: PAGE_SIZE, offset: 0 })
      .then((response) => {
        if (!active) return;
        setItems(response.items);
        setTotal(response.total);
        setOffset(response.items.length);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Unable to load visit history.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await listVisits({ limit: PAGE_SIZE, offset });
      setItems((current) => [...current, ...response.items]);
      setTotal(response.total);
      setOffset((current) => current + response.items.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load more visits.");
    } finally {
      setLoadingMore(false);
    }
  }

  const hasMore = items.length < total;

  // Highest visit_count across loaded rows — surfaced in the live-match
  // strip when > 1 to give a quick "you've been here a lot" signal
  // without cluttering individual rows. Recomputes on Load more.
  const peakVisits = useMemo(
    () => items.reduce((max, item) => Math.max(max, item.visit_count), 0),
    [items],
  );

  return (
    <>
      {/* ── Live-match strip (mirrors master-list / alerts / export) ────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {loading
            ? "Loading…"
            : `${total.toLocaleString()} visited firm${total === 1 ? "" : "s"}`}
        </span>
        {peakVisits > 1 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[#4338ca]">
            Peak {peakVisits.toLocaleString()} visit{peakVisits === 1 ? "" : "s"}
          </span>
        ) : null}
        <span>Read-only — open a firm to add it here.</span>
      </div>

      {error ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {/* ── Visit history SectionPanel ──────────────────────────────────── */}
      <SectionPanel
        eyebrow="History"
        title="Visit history"
        headerAction={
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            {loading
              ? "—"
              : `${total.toLocaleString()} firm${total === 1 ? "" : "s"} recently visited`}
          </span>
        }
      >
        {loading ? (
          <div>
            {Array.from({ length: 4 }).map((_, index) => (
              <div
                key={`visit-loading-${index}`}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <div className="h-3 w-32 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-4 w-48 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-3 w-64 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          <div>
            {items.map((item) => (
              <VisitRow key={item.id} item={item} />
            ))}
          </div>
        )}
      </SectionPanel>

      {hasMore && !loading ? (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={loadingMore}
            className="rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 py-2 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </>
  );
}

function VisitRow({ item }: { item: VisitListItem }) {
  const priorityVar = priorityVariant(item.lead_priority);
  return (
    <div className="flex gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0">
      <span
        aria-hidden
        className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[var(--accent,#6366f1)] shadow-[0_0_0_4px_rgba(99,102,241,0.15)]"
      />
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <Pill variant={healthVariant(item.health_status)}>
            {healthLabel(item.health_status)}
          </Pill>
          {priorityVar ? (
            <Pill variant={priorityVar}>
              {priorityLabel(item.lead_priority)}
              {item.lead_score !== null ? (
                <span className="ml-1 font-mono text-[10px] opacity-80">
                  {item.lead_score.toFixed(0)}
                </span>
              ) : null}
            </Pill>
          ) : (
            <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">Unscored</span>
          )}
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
            <Clock className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
            <span>last visited</span>
            <span className="text-[var(--text-dim,#475569)]">
              {formatRelativeTime(item.last_visited_at)}
            </span>
            <span aria-hidden className="text-[var(--text-dim,#475569)]">·</span>
            <span>{formatVisitCount(item.visit_count)}</span>
          </span>
        </div>
        <Link
          href={`/master-list/${item.id}`}
          className="mb-1 block text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
        >
          {item.name}
        </Link>
        <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          <span>{formatLocation(item.city, item.state)}</span>
          {item.current_clearing_partner ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                Clearing:{" "}
                <span className="text-[var(--text,#0f172a)]">
                  {item.current_clearing_partner}
                </span>
              </span>
            </>
          ) : null}
          {item.latest_net_capital !== null ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                Net capital:{" "}
                <span className="text-[var(--text,#0f172a)]">
                  {formatCurrency(item.latest_net_capital)}
                </span>
              </span>
            </>
          ) : null}
          {item.yoy_growth !== null ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                YoY:{" "}
                <span className="text-[var(--text,#0f172a)]">
                  {formatPercent(item.yoy_growth)}
                </span>
              </span>
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <Clock className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No visits yet
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Open a firm from the master list and it will appear here so you can pick back up
        where you left off.
      </p>
      <Link
        href="/master-list"
        className="mt-5 inline-flex items-center gap-2 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110"
      >
        Browse the master list
      </Link>
    </div>
  );
}
