"use client";

import { AlertCircle, Loader2, RefreshCw, Search } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { FirmRow } from "@/components/outreach-contacts/firm-row";
import { TopActions } from "@/components/layout/top-actions";
import { SectionPanel } from "@/components/ui/section-panel";
import { Segmented } from "@/components/ui/segmented";
import { getPipelineRunStatus } from "@/lib/api";
import {
  gapFillFirmContacts,
  listOutreachContactsFirms,
  type OutreachContactsFirmRow,
  type OutreachEntityKind,
} from "@/lib/outreach-contacts";

type EntityKindFilter = "all" | OutreachEntityKind;

const KIND_FILTER_ITEMS: { value: EntityKindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "broker_dealer", label: "Broker-Dealers" },
  { value: "advisor", label: "Advisors" },
  { value: "institutional_investor", label: "Investors" },
];

function firmKey(firm: { entity_kind: string; entity_id: number }): string {
  return `${firm.entity_kind}:${firm.entity_id}`;
}

export function OutreachContactsClient(): React.ReactElement {
  const [firms, setFirms] = useState<OutreachContactsFirmRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [kindFilter, setKindFilter] = useState<EntityKindFilter>("all");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Per-firm enrich state. Keyed by `${entity_kind}:${entity_id}`.
  const [enrichingKey, setEnrichingKey] = useState<string | null>(null);
  const [enrichNotices, setEnrichNotices] = useState<Record<string, string>>({});
  const [enrichErrors, setEnrichErrors] = useState<Record<string, string>>({});

  // Debounce the search input so we don't refetch on every keystroke.
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(handle);
  }, [query]);

  // Reset to page 1 whenever the filter or query changes.
  useEffect(() => {
    setPage(1);
  }, [kindFilter, debouncedQuery]);

  const loadFirms = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listOutreachContactsFirms({
        entity_kind: kindFilter === "all" ? undefined : kindFilter,
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
  }, [kindFilter, debouncedQuery, page]);

  useEffect(() => {
    void loadFirms();
  }, [loadFirms]);

  const totalPersons = useMemo(
    () => firms.reduce((sum, firm) => sum + firm.contact_count, 0),
    [firms],
  );
  const inFlightCount = useMemo(
    () => firms.filter((f) => f.gap_fill_in_progress).length,
    [firms],
  );

  const totalPages = Math.max(1, Math.ceil(total / 50));

  // POST gap-fill, then poll the run until terminal (180s deadline -- matches
  // the advisor detail page's pattern). On terminal, set a notice from
  // notes.summary and reload the firm list so the next page render shows
  // the fresh emails/phones/LinkedIn URLs in the expanded accordion.
  const pollDeadlineRef = useRef<number | null>(null);
  const handleEnrich = useCallback(
    async (firm: OutreachContactsFirmRow) => {
      const key = firmKey(firm);
      setEnrichingKey(key);
      setEnrichNotices((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setEnrichErrors((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      try {
        const result = await gapFillFirmContacts(firm.entity_kind, firm.entity_id);
        if (result.run_id !== null) {
          const runId = result.run_id;
          pollDeadlineRef.current = Date.now() + 180_000;
          const TERMINAL = new Set([
            "completed",
            "completed_with_errors",
            "failed",
          ]);
          while (
            pollDeadlineRef.current !== null &&
            Date.now() < pollDeadlineRef.current
          ) {
            try {
              const detail = await getPipelineRunStatus(runId);
              if (TERMINAL.has(detail.status)) {
                let summary = `Gap-fill ${detail.status}.`;
                if (detail.notes) {
                  try {
                    const parsed = JSON.parse(detail.notes) as {
                      summary?: unknown;
                    };
                    if (typeof parsed.summary === "string") {
                      summary = parsed.summary;
                    }
                  } catch {
                    /* notes wasn't JSON */
                  }
                }
                setEnrichNotices((prev) => ({ ...prev, [key]: summary }));
                break;
              }
            } catch {
              /* transient poll error */
            }
            await new Promise<void>((resolve) => setTimeout(resolve, 2000));
          }
        } else {
          setEnrichNotices((prev) => ({
            ...prev,
            [key]: result.reason ?? "Gap-fill skipped.",
          }));
        }
        await loadFirms();
      } catch (err) {
        setEnrichErrors((prev) => ({
          ...prev,
          [key]: err instanceof Error ? err.message : "Gap-fill failed.",
        }));
      } finally {
        setEnrichingKey(null);
        pollDeadlineRef.current = null;
      }
    },
    [loadFirms],
  );

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* Topbar */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Outbound{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Contacts
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Enriched contacts by firm
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      {/* Live counts row */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {total.toLocaleString()} firm{total === 1 ? "" : "s"}
        </span>
        {totalPersons > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[#4338ca]">
            {totalPersons.toLocaleString()} contact{totalPersons === 1 ? "" : "s"} on page
          </span>
        ) : null}
        {inFlightCount > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(59,130,246,0.25)] bg-[rgba(59,130,246,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[#1d4ed8]">
            {inFlightCount} enrichment{inFlightCount === 1 ? "" : "s"} running
          </span>
        ) : null}
      </div>

      {/* Search + filter */}
      <div className="mb-4">
        <SectionPanel
          eyebrow="Browse"
          title="Find a firm and enrich its contacts"
        >
          <p className="mb-4 max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Click a firm to expand its contacts. Hit{" "}
            <span className="font-semibold">Enrich all</span> to re-enrich
            every contact on file -- only the missing email / phone /
            LinkedIn channels get filled. Existing data is never overwritten.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label
                htmlFor="outreach-contacts-search"
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
                  id="outreach-contacts-search"
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
            <div className="sm:max-w-md">
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Filter
              </p>
              <Segmented
                value={kindFilter}
                onChange={(value) => setKindFilter(value as EntityKindFilter)}
                items={KIND_FILTER_ITEMS}
                ariaLabel="Filter by entity kind"
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
        title={`Firms with contacts on file${total ? ` (${total.toLocaleString()})` : ""}`}
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
              : "No firms with contacts on file yet."}
          </div>
        ) : (
          <div>
            {firms.map((firm) => {
              const key = firmKey(firm);
              return (
                <FirmRow
                  key={key}
                  firm={firm}
                  isEnriching={enrichingKey === key}
                  enrichNotice={enrichNotices[key] ?? null}
                  enrichError={enrichErrors[key] ?? null}
                  onEnrich={handleEnrich}
                />
              );
            })}
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
