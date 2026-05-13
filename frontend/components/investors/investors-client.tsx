"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { ArrowUpRight, Loader2, MailPlus, Phone, AlertTriangle } from "lucide-react";

import { TopActions } from "@/components/layout/top-actions";
import { apiRequest, buildApiPath } from "@/lib/api";
import { enrichInvestor } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type { InvestorEnrichResponse, InvestorItem, InvestorListResponse } from "@/lib/types";

// ── Tab catalog ───────────────────────────────────────────────────────
// Deshorn (2026-05-12 meeting transcript): two lists, one for
// acquired/buyers (A) and one for disposed/sellers (D). Plus a
// merged "All" tab so the team can scan both in chronological order
// when consolidating per company. Default lands on Buyers — that's
// the primary potential-investor pool (people putting money in).
type InvestorTab = "buyers" | "sellers" | "all";

const TAB_CATALOG: ReadonlyArray<{ value: InvestorTab; label: string }> = [
  { value: "buyers", label: "Buyers (A)" },
  { value: "sellers", label: "Sellers (D)" },
  { value: "all", label: "All" }
];

const TAB_VALUES: ReadonlyArray<InvestorTab> = ["buyers", "sellers", "all"];
const DEFAULT_TAB: InvestorTab = "buyers";

function parseTabParam(raw: string | null): InvestorTab {
  if (raw && (TAB_VALUES as ReadonlyArray<string>).includes(raw)) {
    return raw as InvestorTab;
  }
  return DEFAULT_TAB;
}

const DAYS_OPTIONS = [
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
  { value: 180, label: "Last 180 days" },
  { value: 365, label: "Last year" }
] as const;

export function InvestorsClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const tab = useMemo(() => parseTabParam(searchParams.get("tab")), [searchParams]);

  const [items, setItems] = useState<InvestorItem[]>([]);
  const [ticker, setTicker] = useState("");
  const [tickerDraft, setTickerDraft] = useState("");
  const [days, setDays] = useState<number>(90);
  const [minValue, setMinValue] = useState<number>(50000);
  const [page, setPage] = useState(1);
  const [meta, setMeta] = useState<InvestorListResponse["meta"]>({
    page: 1,
    limit: 20,
    total: 0,
    total_pages: 1
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [enrichingId, setEnrichingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setTab = useCallback(
    (next: InvestorTab) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_TAB) {
        params.delete("tab");
      } else {
        params.set("tab", next);
      }
      const query = params.toString();
      router.replace((query ? `/investors?${query}` : "/investors") as Route, {
        scroll: false
      });
      setPage(1);
    },
    [router, searchParams]
  );

  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/investors", {
        tab: tab === "all" ? undefined : tab,
        ticker: ticker || undefined,
        days,
        min_value: minValue,
        page,
        limit: 20
      }),
    [tab, ticker, days, minValue, page]
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);

    async function load() {
      try {
        const response = await apiRequest<InvestorListResponse>(queryPath);
        if (!active) return;
        setItems(response.items);
        setMeta(response.meta);
      } catch (e) {
        if (!active) return;
        setItems([]);
        setLoadError(e instanceof Error ? e.message : "Unable to load investors.");
      } finally {
        if (active) setLoading(false);
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [queryPath, reloadKey]);

  async function handleEnrich(id: number) {
    setEnrichingId(id);
    setError(null);
    try {
      const result: InvestorEnrichResponse = await enrichInvestor(id);
      setItems((current) =>
        current.map((row) => (row.id === id ? result.item : row))
      );
      if (!result.matched) {
        setError("No contact match returned by Apollo for this person.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Enrichment failed.");
    } finally {
      setEnrichingId(null);
    }
  }

  // Group rows by ticker. The BE already orders by (ticker, transaction_date
  // DESC) so we can stream the items list and break into groups on ticker
  // change without a full re-sort.
  const grouped = useMemo(() => {
    const out: Array<{ ticker: string | null; rows: InvestorItem[] }> = [];
    for (const row of items) {
      const key = row.issuer_ticker || null;
      const last = out[out.length - 1];
      if (last && last.ticker === key) {
        last.rows.push(row);
      } else {
        out.push({ ticker: key, rows: [row] });
      }
    }
    return out;
  }, [items]);

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Investors
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Insider transactions feed
          </h1>
          <p className="mt-1 text-[12px] text-[var(--text-dim,#475569)]">
            Sourced from SEC Form 4 filings. Reporting persons only — issuers
            are intentionally excluded.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <div
          role="tablist"
          aria-label="Investor list"
          className="inline-flex rounded-[12px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-1"
        >
          {TAB_CATALOG.map((entry) => {
            const active = tab === entry.value;
            return (
              <button
                key={entry.value}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTab(entry.value)}
                className={`inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-[13px] transition ${
                  active
                    ? "bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                    : "font-medium text-[var(--text-dim,#475569)] hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
                }`}
              >
                {entry.label}
              </button>
            );
          })}
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {meta.total.toLocaleString()} match{meta.total === 1 ? "" : "es"}
        </span>
      </div>

      {/* Filters */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))"
        }}
      >
        <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_minmax(0,200px)_minmax(0,200px)]">
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Ticker
            </label>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setTicker(tickerDraft.trim());
                setPage(1);
              }}
            >
              <input
                value={tickerDraft}
                onChange={(event) => setTickerDraft(event.target.value)}
                placeholder="AAPL"
                className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] uppercase tracking-[0.04em] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
              />
            </form>
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Window
            </label>
            <select
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value));
                setPage(1);
              }}
              className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            >
              {DAYS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Min transaction ($)
            </label>
            <input
              type="number"
              min={0}
              step={1000}
              value={minValue}
              onChange={(event) => {
                const next = Number(event.target.value);
                setMinValue(Number.isFinite(next) ? Math.max(0, next) : 0);
                setPage(1);
              }}
              className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            />
          </div>
        </div>
      </div>

      {error ? (
        <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {error}
        </div>
      ) : null}

      {/* List */}
      <div
        className="mb-4 overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))"
        }}
      >
        <div className="flex items-center justify-between gap-4 border-b border-[var(--border,rgba(30,64,175,0.1))] px-5 py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Form 4 reporting persons
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              {tab === "buyers"
                ? "Acquired (buyers)"
                : tab === "sellers"
                  ? "Disposed (sellers)"
                  : "All transactions"}
            </h3>
          </div>
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
            {meta.total.toLocaleString()} row{meta.total === 1 ? "" : "s"}
          </span>
        </div>

        <div className="px-5 py-2">
          {loading ? (
            <LoadingSkeleton />
          ) : loadError ? (
            <LoadErrorCard
              message={loadError}
              onRetry={() => setReloadKey((k) => k + 1)}
            />
          ) : items.length === 0 ? (
            <EmptyState />
          ) : (
            <div>
              {grouped.map((group, idx) => (
                <div
                  key={`${group.ticker ?? "no-ticker"}-${idx}`}
                  className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-3 first:border-t-0"
                >
                  <div className="mb-2 flex items-baseline gap-2">
                    <span className="rounded-md bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[12px] font-bold tracking-[0.04em] text-[#312e81]">
                      {group.ticker ?? "—"}
                    </span>
                    <span className="text-[12px] text-[var(--text-dim,#475569)]">
                      {group.rows[0].issuer_name}
                    </span>
                    <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                      {group.rows.length} insider{group.rows.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                    {group.rows.map((row) => (
                      <InvestorRow
                        key={row.id}
                        row={row}
                        enriching={enrichingId === row.id}
                        onEnrich={() => void handleEnrich(row.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Pagination */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
          Showing {meta.total === 0 ? 0 : (meta.page - 1) * meta.limit + 1}–
          {meta.total === 0 ? 0 : Math.min(meta.page * meta.limit, meta.total)} of{" "}
          {meta.total.toLocaleString()}
        </p>
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            disabled={meta.page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Previous
          </button>
          <span className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1.5 text-[12px] font-semibold text-[var(--text,#0f172a)]">
            {meta.page} / {meta.total_pages}
          </span>
          <button
            type="button"
            disabled={meta.page >= meta.total_pages}
            onClick={() =>
              setPage((current) => Math.min(meta.total_pages, current + 1))
            }
            className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

function InvestorRow({
  row,
  enriching,
  onEnrich
}: {
  row: InvestorItem;
  enriching: boolean;
  onEnrich: () => void;
}) {
  const adChipClass =
    row.ad_code === "A"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-red-200 bg-red-50 text-red-700";
  const adLabel = row.ad_code === "A" ? "Acquired" : "Disposed";
  const address = [
    row.reporting_owner_street1,
    row.reporting_owner_street2,
    [row.reporting_owner_city, row.reporting_owner_state, row.reporting_owner_zip]
      .filter((s) => s && s.trim().length > 0)
      .join(", ")
  ]
    .filter((s) => s && s.trim().length > 0)
    .join(" • ");
  const hasEnrichment = !!row.enriched_at;

  return (
    <div className="grid gap-3 py-3 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,160px)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-semibold text-[var(--text,#0f172a)]">
            {row.reporting_owner_name}
          </span>
          {row.reporting_owner_title ? (
            <span className="text-[12px] text-[var(--text-dim,#475569)]">
              {row.reporting_owner_title}
            </span>
          ) : null}
          <span
            className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] ${adChipClass}`}
          >
            {adLabel}
          </span>
        </div>
        <p className="mt-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
          {address || "No address on filing"}
        </p>
        {hasEnrichment ? (
          <div className="mt-1.5 flex flex-wrap gap-3 text-[12px] text-[var(--text-dim,#475569)]">
            {row.enriched_phone ? (
              <span className="inline-flex items-center gap-1">
                <Phone className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {row.enriched_phone}
              </span>
            ) : null}
            {row.enriched_email ? (
              <a
                href={`mailto:${row.enriched_email}`}
                className="inline-flex items-center gap-1 text-[#6366f1] hover:underline"
              >
                <MailPlus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                {row.enriched_email}
              </a>
            ) : null}
            {!row.enriched_phone && !row.enriched_email ? (
              <span className="text-[11px] italic text-[var(--text-muted,#94a3b8)]">
                Apollo returned no match
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="min-w-0 text-[12px] text-[var(--text-dim,#475569)]">
        <div>
          <span className="font-semibold text-[var(--text,#0f172a)]">
            {formatCurrency(row.transaction_value)}
          </span>
          <span className="ml-1 text-[var(--text-muted,#94a3b8)]">
            ({row.shares?.toLocaleString() ?? "—"} sh
            {row.price_per_share
              ? ` @ ${formatCurrency(row.price_per_share)}`
              : ""}
            )
          </span>
        </div>
        <div className="text-[11px] text-[var(--text-muted,#94a3b8)]">
          {formatDate(row.transaction_date)} • {row.security_title ?? "—"}
        </div>
        {row.source_filing_url ? (
          <a
            href={row.source_filing_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-[#6366f1] hover:underline"
          >
            View Form 4 <ArrowUpRight className="h-3 w-3" strokeWidth={2} />
          </a>
        ) : null}
      </div>
      <div className="flex items-start justify-end">
        <button
          type="button"
          onClick={onEnrich}
          disabled={enriching}
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {enriching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : null}
          {hasEnrichment ? "Re-enrich" : "Find contact"}
        </button>
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3 py-4">
      {[0, 1, 2, 3].map((idx) => (
        <div
          key={idx}
          className="h-16 animate-pulse rounded-xl bg-[var(--surface-2,#f1f6fd)]"
        />
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="my-6 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <Link
        href={"/settings/pipelines" as Route}
        className="text-[14px] font-semibold text-[var(--text,#0f172a)] hover:text-[#6366f1]"
      >
        No Form 4 transactions yet
      </Link>
      <p className="mx-auto mt-2 max-w-md text-[12px] text-[var(--text-dim,#475569)]">
        Run the Form 4 watcher from Pipelines, or wait for the daily cron to
        populate the feed. Empty results may also mean no filings cleared the
        current value floor in the selected window.
      </p>
    </div>
  );
}

function LoadErrorCard({
  message,
  onRetry
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="my-4 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[rgba(239,68,68,0.1)] text-[var(--pill-red-text,#b91c1c)]">
        <AlertTriangle className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        Couldn&apos;t load investors
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-5 inline-flex h-[34px] items-center rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 text-[13px] font-semibold text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)]"
      >
        Retry
      </button>
    </div>
  );
}
