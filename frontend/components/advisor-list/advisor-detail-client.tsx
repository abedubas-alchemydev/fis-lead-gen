"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { ArrowLeft, ArrowRight, ExternalLink, Globe, Search } from "lucide-react";

import {
  apiRequest,
  buildApiPath,
  getPipelineRunStatus,
  refreshAdvisor,
} from "@/lib/api";
import { PageSpinner } from "@/components/ui/spinner";
import {
  buildAdvisorListUrl,
  encodeReturnParam,
  parseReturnParam,
  ADVISOR_LIST_STATE_DEFAULTS,
  type AdvisorListQueryState,
} from "@/lib/advisor-list-state";
import { formatCurrency, formatDate } from "@/lib/format";
import { ListPicker } from "@/components/list-picker/list-picker";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import type {
  InvestmentAdvisorListResponse,
  InvestmentAdvisorProfileResponse,
} from "@/lib/types";

// Secondary button preset — copied from broker-dealer-detail-client.tsx so the
// Previous/Next nav buttons match the master-list detail page exactly.
const SECONDARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 py-2 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45";

// Compact website display: strip protocol/www/trailing slash and take the
// first path segment. Mirrors ResolvedLink in firm-website-link.tsx.
function cleanWebsiteDisplay(website: string): string {
  return (
    website
      .replace(/^https?:\/\//i, "")
      .replace(/^www\./i, "")
      .replace(/\/+$/, "")
      .split("/")[0]
      ?.toLowerCase() ?? website
  );
}

// Builds the same /api/v1/investment-advisors query the advisor workspace
// emits, from a recovered AdvisorListQueryState, so Next/Previous walk the
// *exact* same result set the user was looking at when they clicked in. Keep
// in lock step with the params object in advisor-list-workspace-client.tsx.
function listApiPathFromState(
  state: AdvisorListQueryState,
  pageOverride?: number,
): string {
  const params: Record<string, string | number | string[]> = {
    sort_by: state.sortBy,
    sort_dir: state.sortDir,
    page: pageOverride ?? state.page,
    limit: state.limit,
  };
  if (state.search) params.q = state.search;
  if (state.state) params.state = [state.state];
  if (state.status !== "All") params.status = [state.status];
  // BE defaults files_13f=true, so only send the explicit "false" override.
  if (state.filesThirteenF === "all") params.files_13f = "false";
  if (state.advisoryActivities.length > 0) {
    params.advisory_activities = state.advisoryActivities;
  }
  if (state.clientTypes.length > 0) {
    params.client_types = state.clientTypes;
  }
  if (state.minRegulatoryAum !== null) {
    params.min_regulatory_aum = state.minRegulatoryAum;
  }
  if (state.maxRegulatoryAum !== null) {
    params.max_regulatory_aum = state.maxRegulatoryAum;
  }
  if (state.registeredAfter !== null) {
    params.registered_after = state.registeredAfter;
  }
  if (state.registeredBefore !== null) {
    params.registered_before = state.registeredBefore;
  }
  return buildApiPath("/api/v1/investment-advisors", params);
}

// Detail view for /advisor-list/{id}. Mirrors the design system used on
// /master-list/{id}: page topbar with breadcrumbs + h1 + meta line, KPI
// strip of MiniStat tiles, and SectionPanel cards on a 2-column grid.
export function AdvisorDetailClient({ advisorId }: { advisorId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [data, setData] = useState<InvestmentAdvisorProfileResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [prevId, setPrevId] = useState<number | null>(null);
  const [nextId, setNextId] = useState<number | null>(null);

  // Refresh-on-visit gating. Mirrors broker-dealer-detail-client.tsx — we
  // POST /refresh-all on mount so the BE's per-pipeline gates can fill any
  // missing column (executive_officers, website) before we render. The
  // /profile fetch below is gated on `refreshState.phase === "ready"` so
  // the user sees the loading screen until the orchestrator's child
  // pipelines finish (or short-circuit). Errors fall through to "ready" —
  // refresh is best-effort, never blocks the page indefinitely.
  type RefreshPhase =
    | { phase: "queuing" }
    | { phase: "polling"; runId: number; pipelinesRunning: string[] }
    | { phase: "ready" };
  const [refreshState, setRefreshState] = useState<RefreshPhase>({ phase: "queuing" });

  // Refresh-on-visit: POST /refresh-all and poll the parent PipelineRun
  // until terminal. Same handler shape + 180s poll deadline as the BD
  // detail page (see broker-dealer-detail-client.tsx, PR #482).
  useEffect(() => {
    const numericId = Number(advisorId);
    if (!Number.isFinite(numericId)) {
      setRefreshState({ phase: "ready" });
      return;
    }
    let active = true;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    function parsePipelinesRunning(notes: string | null): string[] {
      if (!notes) return [];
      try {
        const parsed = JSON.parse(notes) as { ran?: unknown };
        if (Array.isArray(parsed.ran)) {
          return parsed.ran.filter((x): x is string => typeof x === "string");
        }
      } catch {
        /* notes isn't structured JSON yet (early in lifecycle) */
      }
      return [];
    }

    async function pollUntilTerminal(runId: number) {
      const deadline = Date.now() + 180_000;
      const TERMINAL = new Set(["completed", "completed_with_errors", "failed"]);
      while (active && Date.now() < deadline) {
        try {
          const detail = await getPipelineRunStatus(runId);
          if (!active) return;
          if (TERMINAL.has(detail.status)) {
            setRefreshState({ phase: "ready" });
            return;
          }
          setRefreshState({
            phase: "polling",
            runId,
            pipelinesRunning: parsePipelinesRunning(detail.notes),
          });
        } catch {
          // Transient poll error — wait and try again.
        }
        await new Promise<void>((resolve) => {
          pollTimer = setTimeout(resolve, 2000);
        });
      }
      if (active) setRefreshState({ phase: "ready" });
    }

    async function run() {
      try {
        const result = await refreshAdvisor(numericId, "all");
        if (!active) return;
        if (result.status === "skipped" || result.run_id === null) {
          setRefreshState({ phase: "ready" });
          return;
        }
        setRefreshState({
          phase: "polling",
          runId: result.run_id,
          pipelinesRunning: [],
        });
        await pollUntilTerminal(result.run_id);
      } catch {
        // 429 / 503 / network — fall through so the page renders.
        if (active) setRefreshState({ phase: "ready" });
      }
    }
    void run();

    return () => {
      active = false;
      if (pollTimer !== null) clearTimeout(pollTimer);
    };
  }, [advisorId]);

  useEffect(() => {
    // Wait for refresh-on-visit to finish before fetching /profile.
    if (refreshState.phase !== "ready") return;
    apiRequest<InvestmentAdvisorProfileResponse>(
      `/api/v1/investment-advisors/${advisorId}/profile`,
    )
      .then(setData)
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load advisor");
      });
  }, [advisorId, refreshState.phase]);

  // Restore the user's filter/sort state on back-nav, falling back to
  // the bare list URL if no return envelope was passed.
  const returnState = useMemo(
    () => parseReturnParam(searchParams.get("return")),
    [searchParams],
  );
  const returnEnvelope = useMemo(
    () => (returnState ? encodeReturnParam(returnState) : ""),
    [returnState],
  );
  const backHref = (
    returnState
      ? buildAdvisorListUrl(returnState)
      : buildAdvisorListUrl(ADVISOR_LIST_STATE_DEFAULTS)
  ) as Route;

  // Resolve adjacent advisor IDs for Previous/Next. With a return envelope,
  // walk the same filtered/sorted page the user came from and step ±1 (fetching
  // the neighbouring page at a boundary); without one (deep link), fall back to
  // the global /adjacent order. Mirrors broker-dealer-detail-client.tsx.
  useEffect(() => {
    let active = true;
    const numericId = Number(advisorId);

    async function resolveFromAdjacent() {
      try {
        const adj = await apiRequest<{
          prev_id: number | null;
          next_id: number | null;
        }>(`/api/v1/investment-advisors/${advisorId}/adjacent`);
        if (!active) return;
        setPrevId(adj.prev_id);
        setNextId(adj.next_id);
      } catch {
        if (active) {
          setPrevId(null);
          setNextId(null);
        }
      }
    }

    async function resolveFromReturnState(state: AdvisorListQueryState) {
      const response = await apiRequest<InvestmentAdvisorListResponse>(
        listApiPathFromState(state),
      );
      if (!active) return;

      const idx = response.items.findIndex((item) => item.id === numericId);
      if (idx === -1) {
        // The advisor dropped out of the user's view (data refresh / filter
        // change). Fall back to the global walker so the buttons still work.
        await resolveFromAdjacent();
        return;
      }

      let prev: number | null = null;
      let next: number | null = null;

      if (idx > 0) {
        prev = response.items[idx - 1].id;
      } else if (response.meta.page > 1) {
        const prevPage = await apiRequest<InvestmentAdvisorListResponse>(
          listApiPathFromState(state, response.meta.page - 1),
        );
        if (!active) return;
        if (prevPage.items.length > 0) {
          prev = prevPage.items[prevPage.items.length - 1].id;
        }
      }

      if (idx < response.items.length - 1) {
        next = response.items[idx + 1].id;
      } else if (response.meta.page < response.meta.total_pages) {
        const nextPage = await apiRequest<InvestmentAdvisorListResponse>(
          listApiPathFromState(state, response.meta.page + 1),
        );
        if (!active) return;
        if (nextPage.items.length > 0) {
          next = nextPage.items[0].id;
        }
      }

      setPrevId(prev);
      setNextId(next);
    }

    if (returnState && Number.isFinite(numericId)) {
      void resolveFromReturnState(returnState).catch(() => {
        if (active) void resolveFromAdjacent();
      });
    } else {
      void resolveFromAdjacent();
    }

    return () => {
      active = false;
    };
  }, [advisorId, returnState]);

  // Same-shape /advisor-list/{id} link that preserves the return envelope so
  // chaining Next/Previous keeps the user's filtered context.
  const buildAdjacentHref = (id: number): Route => {
    const base = `/advisor-list/${id}`;
    return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
  };

  // Refresh-on-visit gate. Show loading screen while the BE orchestrator
  // is queuing or running. Once ready, the /profile fetch above populates
  // and the existing render path runs.
  if (refreshState.phase === "queuing") {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <PageSpinner label="Preparing fresh data for this advisor…" />
      </div>
    );
  }
  if (refreshState.phase === "polling") {
    const label =
      refreshState.pipelinesRunning.length > 0
        ? `Refreshing ${refreshState.pipelinesRunning.join(", ")}…`
        : "Refreshing advisor data…";
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <PageSpinner label={label} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          Couldn&apos;t load advisor profile: {error}
        </div>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div
          className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8"
          style={{
            boxShadow:
              "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
          }}
        >
          <div className="h-6 w-56 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-4 h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-8 grid gap-4 xl:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]"
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  const { advisor, contacts, filings } = data;
  const location = [advisor.city, advisor.state].filter(Boolean).join(", ");
  const directOwners = advisor.direct_owners ?? [];
  const indirectOwners = advisor.indirect_owners ?? [];
  const executiveOfficers = advisor.executive_officers ?? [];
  const advisoryActivities = advisor.advisory_activities ?? [];
  const clientTypes = advisor.client_types ?? [];
  const clientCounts = advisor.client_counts ?? null;

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* ── Topbar: breadcrumbs + h1 + meta ─────────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={backHref}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Investment Advisors
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Firm
            Detail
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
              {advisor.name}
            </h1>
            <ListPicker
              firmId={advisor.id}
              variant="detail"
              entityType="advisor"
              initialFavorited={data.is_favorited}
            />
          </div>
          {advisor.legal_name && advisor.legal_name !== advisor.name ? (
            <p className="mt-1 text-[13px] text-[var(--text-dim,#475569)]">
              Legal name:{" "}
              <span className="font-medium text-[var(--text,#0f172a)]">
                {advisor.legal_name}
              </span>
            </p>
          ) : null}
          {/* Website + Google fallback — header-level, mirrors master-list. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {advisor.website ? (
              <a
                href={
                  advisor.website.startsWith("http")
                    ? advisor.website
                    : `https://${advisor.website}`
                }
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[13px] text-[var(--accent,#6366f1)] transition hover:underline"
              >
                <Globe className="h-3.5 w-3.5" strokeWidth={2} />
                {cleanWebsiteDisplay(advisor.website)}
              </a>
            ) : null}
            <a
              href={`https://www.google.com/search?q=${encodeURIComponent(`${advisor.name} investment advisor`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)] hover:underline"
            >
              <Search className="h-3.5 w-3.5" strokeWidth={2} />
              Search Google for this firm
            </a>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {advisor.crd_number ? (
              <span>
                CRD{" "}
                <span className="font-mono text-[var(--text-dim,#475569)]">
                  {advisor.crd_number}
                </span>
              </span>
            ) : null}
            {advisor.cik ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  CIK{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {advisor.cik}
                  </span>
                </span>
              </>
            ) : null}
            {advisor.sec_file_number ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  SEC File{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {advisor.sec_file_number}
                  </span>
                </span>
              </>
            ) : null}
            {location ? (
              <>
                <span aria-hidden>·</span>
                <span>{location}</span>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── Status pills row ────────────────────────────────────────────── */}
      {advisor.files_13f ? (
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Pill variant="healthy">13F filer</Pill>
        </div>
      ) : null}

      {/* ── Prev / Back / Next nav row ──────────────────────────────────── */}
      <div className="mb-5 flex items-center justify-between gap-3">
        <button
          type="button"
          disabled={!prevId}
          onClick={() => prevId && router.push(buildAdjacentHref(prevId))}
          className={SECONDARY_BTN}
        >
          <ArrowLeft className="h-4 w-4" strokeWidth={2} aria-hidden />
          Previous
        </button>
        <Link
          href={backHref}
          className="text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          Back to advisors
        </Link>
        <button
          type="button"
          disabled={!nextId}
          onClick={() => nextId && router.push(buildAdjacentHref(nextId))}
          className={SECONDARY_BTN}
        >
          Next
          <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden />
        </button>
      </div>

      {/* ── KPI strip ───────────────────────────────────────────────────── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Regulatory AUM"
          value={formatCurrency(advisor.regulatory_aum)}
        />
        <MiniStat
          label="Total clients"
          value={advisor.total_clients?.toLocaleString() ?? "—"}
        />
        <MiniStat
          label="Last filing"
          value={formatDate(advisor.last_filing_date)}
        />
        <MiniStat
          label="Latest 13F"
          value={formatDate(advisor.latest_13f_filing_date)}
        />
      </div>

      {/* ── 2-column section grid ───────────────────────────────────────── */}
      <div className="grid gap-4 xl:grid-cols-2">
        {/* Form ADV details */}
        <SectionPanel
          eyebrow="Form ADV"
          title="Advisory activities & client mix"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Discretionary AUM"
              value={formatCurrency(advisor.discretionary_aum)}
              compact
            />
            <MiniStat
              label="Non-discretionary AUM"
              value={formatCurrency(advisor.non_discretionary_aum)}
              compact
            />
          </div>

          <div className="mt-4">
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              Advisory activities
            </p>
            {advisoryActivities.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {advisoryActivities.map((activity) => (
                  <Pill key={activity} variant="info">
                    {activity}
                  </Pill>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">
                Not extracted yet.
              </p>
            )}
          </div>

          <div className="mt-4">
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              Client types
            </p>
            {clientTypes.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {clientTypes.map((type) => (
                  <span
                    key={type}
                    className="rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1 text-[11px] text-[var(--text-dim,#475569)]"
                  >
                    {type}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">
                Not extracted yet.
              </p>
            )}
          </div>

          {clientCounts && Object.keys(clientCounts).length > 0 ? (
            <div className="mt-4 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Client counts (Item 5.D.3)
              </p>
              <dl className="mt-2 grid gap-x-4 gap-y-1 text-[13px] text-[var(--text-dim,#475569)] sm:grid-cols-2">
                {Object.entries(clientCounts).map(([key, count]) => (
                  <div
                    key={key}
                    className="flex items-baseline justify-between gap-2"
                  >
                    <dt className="capitalize">{key.replace(/_/g, " ")}</dt>
                    <dd className="font-mono tabular-nums text-[var(--text,#0f172a)]">
                      {count.toLocaleString()}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          {advisor.firm_operations_text ? (
            <p className="mt-4 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
              {advisor.firm_operations_text}
            </p>
          ) : null}
        </SectionPanel>

        {/* Firm overview */}
        <SectionPanel eyebrow="Overview" title="Registration & filings">
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Status"
              value={advisor.status || "—"}
              compact
            />
            <MiniStat
              label="Registration date"
              value={formatDate(advisor.registration_date)}
              compact
            />
            <MiniStat
              label="Formation date"
              value={formatDate(advisor.formation_date)}
              compact
            />
            <MiniStat
              label="Source"
              value={advisor.matched_source || "—"}
              compact
            />
          </div>

          {advisor.filings_index_url ? (
            <a
              href={advisor.filings_index_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-4 inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
              EDGAR filings index
            </a>
          ) : null}
        </SectionPanel>

        {/* People */}
        <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
          {directOwners.length === 0 &&
          executiveOfficers.length === 0 &&
          indirectOwners.length === 0 &&
          contacts.length === 0 ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No owners, officers, or contacts on file yet.
            </p>
          ) : null}

          {directOwners.length > 0 ? (
            <PeopleSubGroup title="Direct owners">
              {directOwners.map((owner, i) => (
                <PersonCard
                  key={`direct-${i}`}
                  name={owner.name ?? "—"}
                  title={owner.title ?? null}
                  extra={
                    owner.ownership_pct
                      ? `Ownership: ${owner.ownership_pct}`
                      : null
                  }
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {executiveOfficers.length > 0 ? (
            <PeopleSubGroup title="Executive officers">
              {executiveOfficers.map((officer, i) => (
                <PersonCard
                  key={`officer-${i}`}
                  name={officer.name ?? "—"}
                  title={officer.title ?? null}
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {indirectOwners.length > 0 ? (
            <PeopleSubGroup title="Indirect owners">
              {indirectOwners.map((owner, i) => (
                <PersonCard
                  key={`indirect-${i}`}
                  name={owner.name ?? "—"}
                  title={owner.title ?? null}
                  extra={
                    owner.ownership_pct
                      ? `Ownership: ${owner.ownership_pct}`
                      : null
                  }
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {contacts.length > 0 ? (
            <PeopleSubGroup title="Enriched contacts">
              {contacts.map((contact) => (
                <PersonCard
                  key={`contact-${contact.id}`}
                  name={contact.name}
                  title={contact.title}
                  email={contact.email}
                />
              ))}
            </PeopleSubGroup>
          ) : null}
        </SectionPanel>

        {/* Filings */}
        <SectionPanel eyebrow="Filings" title="Recent regulatory filings">
          {filings.length === 0 ? (
            <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
              No filings tracked yet.
            </div>
          ) : (
            <div className="space-y-2">
              {filings.map((filing) => (
                <div
                  key={filing.id}
                  className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-[var(--text,#0f172a)]">
                        {filing.form_type}
                      </p>
                      {filing.summary ? (
                        <p className="mt-1 text-sm text-[var(--text-dim,#475569)]">
                          {filing.summary}
                        </p>
                      ) : null}
                    </div>
                    {filing.priority ? (
                      <Pill variant="info">{filing.priority}</Pill>
                    ) : null}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-4 text-[12px] text-[var(--text-muted,#94a3b8)]">
                    <span>{formatDate(filing.filed_at)}</span>
                    {filing.source_filing_url ? (
                      <a
                        href={filing.source_filing_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-[var(--accent,#6366f1)] hover:underline"
                      >
                        Open filing
                        <ExternalLink className="h-3 w-3" strokeWidth={2} />
                      </a>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionPanel>
      </div>
    </div>
  );
}

// Inline mini-stat card. Mirrors the MiniStat helper inside
// broker-dealer-detail-client.tsx so KPI tiles look identical across the
// two detail pages (surface-2 background, eyebrow label, semibold value).
function MiniStat({
  label,
  value,
  helper,
  compact,
}: {
  label: string;
  value: React.ReactNode;
  helper?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 ${
        compact ? "py-3" : "py-4"
      } text-sm`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p
        className={`mt-1 ${
          compact
            ? "text-[13px] text-[var(--text,#0f172a)]"
            : "text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]"
        }`}
      >
        {value}
      </p>
      {helper ? (
        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
          {helper}
        </p>
      ) : null}
    </div>
  );
}

function PeopleSubGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4 last:mb-0">
      <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
        {title}
      </p>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function PersonCard({
  name,
  title,
  email,
  extra,
  source,
}: {
  name: string;
  title?: string | null;
  email?: string | null;
  extra?: string | null;
  source?: string;
}) {
  return (
    <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
      <p className="font-semibold text-[var(--text,#0f172a)]">{name}</p>
      {title ? <p className="mt-1">{title}</p> : null}
      {email ? (
        <p className="mt-1">
          <a
            href={`mailto:${email}`}
            className="text-[var(--accent,#6366f1)] hover:underline"
          >
            {email}
          </a>
        </p>
      ) : null}
      {extra ? (
        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
          {extra}
        </p>
      ) : null}
      {source ? (
        <p className="mt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
          {source}
        </p>
      ) : null}
    </div>
  );
}
