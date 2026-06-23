"use client";

import { AlertCircle, Globe, Loader2, Mail, RefreshCw, Search, Sparkles, Users } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
} from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { KpiCard, type KpiIconProps } from "@/components/dashboard/kpi-card";
import { FirmExtractionRowItem } from "@/components/settings/extractions/firm-extraction-row";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { Segmented } from "@/components/ui/segmented";
import {
  getExtractionSummary,
  listExtractionFirms,
  type FirmExtractionRow,
  type FirmType,
  type ProviderCount,
  type ProviderSummaryResponse,
} from "@/lib/extraction-analytics";

type FirmTypeFilter = "all" | FirmType;

const KIND_FILTER_ITEMS: { value: FirmTypeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "broker_dealer", label: "Broker-Dealers" },
  { value: "advisor", label: "Advisors" },
  { value: "institutional_investor", label: "Investors" },
];

function firmKey(firm: FirmExtractionRow): string {
  return `${firm.firm_type}:${firm.firm_id}`;
}

// KpiCard wants ComponentType<{className?; strokeWidth?: number}>. Lucide's own
// type widens strokeWidth to string|number, so wrap each icon to the exact
// shape rather than casting.
const UsersKpiIcon = (props: KpiIconProps) => <Users {...props} />;
const MailKpiIcon = (props: KpiIconProps) => <Mail {...props} />;
const SparklesKpiIcon = (props: KpiIconProps) => <Sparkles {...props} />;
const GlobeKpiIcon = (props: KpiIconProps) => <Globe {...props} />;

function ProviderRow({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: ProviderCount[];
  emptyLabel: string;
}): React.ReactElement {
  return (
    <div className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-3 first:border-t-0">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {title}
      </p>
      {items.length === 0 ? (
        <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">{emptyLabel}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {items.map((p) => (
            <Pill key={`${p.surface}-${p.provider}`} variant="info">
              {p.label}
              <span className="tabular-nums opacity-70">
                {p.value_count.toLocaleString()}
              </span>
              <span className="text-[10px] font-normal opacity-60">
                · {p.firm_count.toLocaleString()} firms
              </span>
            </Pill>
          ))}
        </div>
      )}
    </div>
  );
}

export function ExtractionAnalyticsClient(): React.ReactElement {
  const [summary, setSummary] = useState<ProviderSummaryResponse | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [firms, setFirms] = useState<FirmExtractionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [firmTypeFilter, setFirmTypeFilter] = useState<FirmTypeFilter>("all");
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Summary loads once on mount — it's account-wide, not filter-dependent.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await getExtractionSummary();
        if (!cancelled) setSummary(response);
      } catch (err) {
        if (!cancelled) {
          setSummaryError(
            err instanceof Error ? err.message : "Could not load summary",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounce the search input so we don't refetch on every keystroke.
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(handle);
  }, [query]);

  // Reset to page 1 whenever a filter or the query changes.
  useEffect(() => {
    setPage(1);
  }, [firmTypeFilter, providerFilter, debouncedQuery]);

  const loadFirms = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listExtractionFirms({
        firm_type: firmTypeFilter === "all" ? undefined : firmTypeFilter,
        provider: providerFilter || undefined,
        q: debouncedQuery || undefined,
        page,
        limit: 50,
      });
      setFirms(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load firms");
    } finally {
      setLoading(false);
    }
  }, [firmTypeFilter, providerFilter, debouncedQuery, page]);

  useEffect(() => {
    void loadFirms();
  }, [loadFirms]);

  // De-duplicated provider list for the filter dropdown. A provider that
  // appears in more than one surface (e.g. Hunter in contacts + emails) is
  // shown once; the count sums its surfaces.
  const providerOptions = useMemo(() => {
    if (!summary) return [];
    const map = new Map<string, { label: string; count: number }>();
    for (const p of [
      ...summary.contacts,
      ...summary.discovered_emails,
      ...summary.websites,
    ]) {
      const current = map.get(p.provider);
      if (current) {
        current.count += p.value_count;
      } else {
        map.set(p.provider, { label: p.label, count: p.value_count });
      }
    }
    return Array.from(map.entries())
      .map(([provider, value]) => ({ provider, ...value }))
      .sort((a, b) => b.count - a.count);
  }, [summary]);

  const totalPages = Math.max(1, Math.ceil(total / 50));

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      {/* Topbar */}
      <div className="mb-7">
        <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
          Settings <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
          Extraction Analytics
        </p>
        <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          What each provider extracted
        </h1>
      </div>

      {summaryError ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>{summaryError}</span>
        </div>
      ) : null}

      {/* KPI strip */}
      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Contacts attributed"
          value={(summary?.totals.total_contacts_attributed ?? 0).toLocaleString()}
          helper={
            summary
              ? `${summary.totals.total_contacts_unattributed.toLocaleString()} unattributed (legacy)`
              : "Loading…"
          }
          tone="blue"
          icon={UsersKpiIcon}
        />
        <KpiCard
          title="Discovered emails"
          value={(summary?.totals.total_discovered_emails ?? 0).toLocaleString()}
          helper="Across the email-extractor providers"
          tone="purple"
          icon={MailKpiIcon}
        />
        <KpiCard
          title="Enriched emails"
          value={(summary?.totals.total_enriched_emails ?? 0).toLocaleString()}
          helper="Apollo reverse-email matches"
          tone="amber"
          icon={SparklesKpiIcon}
        />
        <KpiCard
          title="Firms with a website"
          value={(summary?.totals.total_firms_with_website ?? 0).toLocaleString()}
          helper="Resolved or regulator-sourced"
          tone="blue"
          icon={GlobeKpiIcon}
        />
      </div>

      {/* Provider summary */}
      <div className="mb-4">
        <SectionPanel eyebrow="Providers" title="Extraction by provider">
          <p className="mb-2 max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Counts reflect the winning provider per contact. Expand a firm
            below for per-value provenance (an individual email or phone can
            come from a different provider than the one that won the contact).
          </p>
          {summary ? (
            <div>
              <ProviderRow
                title="Contacts"
                items={summary.contacts}
                emptyLabel="No attributed contacts yet."
              />
              <ProviderRow
                title="Discovered emails"
                items={summary.discovered_emails}
                emptyLabel="No discovered emails yet."
              />
              <ProviderRow
                title="Websites"
                items={summary.websites}
                emptyLabel="No website provenance yet."
              />
            </div>
          ) : (
            <div className="flex items-center gap-2 py-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
              Loading provider summary…
            </div>
          )}
        </SectionPanel>
      </div>

      {/* Filter bar */}
      <div className="mb-4">
        <SectionPanel eyebrow="Browse" title="Find a firm and inspect its extractions">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
            <div className="flex-1">
              <label
                htmlFor="extraction-search"
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
              >
                Search firm name
              </label>
              <div className="relative">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted,#94a3b8)]"
                  strokeWidth={2}
                />
                <input
                  id="extraction-search"
                  type="text"
                  value={query}
                  onChange={(event: ChangeEvent<HTMLInputElement>) =>
                    setQuery(event.target.value)
                  }
                  placeholder="Search by firm name…"
                  className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] pl-9 pr-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
                />
              </div>
            </div>
            <div>
              <label
                htmlFor="extraction-provider"
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
              >
                Provider
              </label>
              <select
                id="extraction-provider"
                value={providerFilter}
                onChange={(event) => setProviderFilter(event.target.value)}
                className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] lg:w-56"
              >
                <option value="">All providers</option>
                {providerOptions.map((option) => (
                  <option key={option.provider} value={option.provider}>
                    {option.label} ({option.count.toLocaleString()})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Firm type
              </p>
              <Segmented
                value={firmTypeFilter}
                onChange={(value) => setFirmTypeFilter(value as FirmTypeFilter)}
                items={KIND_FILTER_ITEMS}
                ariaLabel="Filter by firm type"
              />
            </div>
          </div>
        </SectionPanel>
      </div>

      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>{error}</span>
        </div>
      ) : null}

      {/* Firms list */}
      <SectionPanel
        eyebrow="Firms"
        title={`Firms with extractions${total ? ` (${total.toLocaleString()})` : ""}`}
        headerAction={
          <button
            type="button"
            onClick={() => void loadFirms()}
            disabled={loading}
            className={clsx(
              buttonBase,
              buttonSizes.sm,
              "border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]",
            )}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              strokeWidth={2}
            />
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      >
        {loading && firms.length === 0 ? (
          <div>
            {Array.from({ length: 6 }).map((_, index) => (
              <div
                key={`firm-skeleton-${index}`}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <div className="h-3 w-32 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-4 w-64 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-3 w-48 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
            ))}
          </div>
        ) : firms.length === 0 ? (
          <div className="py-8 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
            <Loader2
              className={`mx-auto mb-2 h-5 w-5 ${loading ? "animate-spin" : "opacity-0"}`}
              strokeWidth={2}
            />
            {debouncedQuery
              ? `No firms match "${debouncedQuery}".`
              : "No firms with extractions match these filters."}
          </div>
        ) : (
          <div>
            {firms.map((firm) => (
              <FirmExtractionRowItem key={firmKey(firm)} firm={firm} />
            ))}
          </div>
        )}

        {totalPages > 1 ? (
          <div className="mt-4 flex items-center justify-between border-t border-[var(--border,rgba(30,64,175,0.1))] pt-4 text-[12px]">
            <span className="text-[var(--text-muted,#94a3b8)]">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </SectionPanel>
    </div>
  );
}
