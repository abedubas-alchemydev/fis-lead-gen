"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Route } from "next";

import {
  ArrowLeft,
  ArrowRight,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { ArrangementFields } from "@/components/master-list/detail/arrangement-fields";
import { ContactRow } from "@/components/master-list/detail/contact-row";
import { FinancialTrendChart } from "@/components/master-list/detail/financial-trend-chart";
import { FindEmailsButton } from "@/components/master-list/detail/find-emails-button";
import { FirmWebsiteLink } from "@/components/master-list/detail/firm-website-link";
import { RefreshFirmButton } from "@/components/master-list/detail/refresh-firm-button";
import { FocusReportSection } from "@/components/master-list/detail/focus-report-section";
import {
  classificationDisplay,
  clearingTypeLabel,
  clearingTypeVariant,
  healthLabel,
  healthVariant,
  priorityLabel,
  priorityVariant,
} from "@/components/master-list/detail/pill-helpers";
import {
  dedupOfficers,
  nameMatches,
  toOfficerEntity,
} from "@/components/master-list/detail/name-matching";
import { SectionPanel } from "@/components/ui/section-panel";
import { ListPicker } from "@/components/list-picker/list-picker";
import { Pill } from "@/components/ui/pill";
import { SourceBadge } from "@/components/master-list/source-badge";
import { UnknownCell } from "@/components/master-list/unknown-cell";
import { apiRequest, buildApiPath } from "@/lib/api";
import { isFirmIncomplete } from "@/lib/firm-completeness";
import { parseArrangementBlob } from "@/lib/arrangements";
import {
  recordVisit,
  type FavoriteListResponse,
  type VisitListResponse,
} from "@/lib/favorites";
import { formatCurrency, formatDate, formatPercent, viewableFilingUrl } from "@/lib/format";
import {
  buildSourceListUrl,
  encodeReturnParam,
  parseReturnParam,
  type DetailSource,
  type MasterListQueryState,
} from "@/lib/master-list-state";
import { stateCodeFromName } from "@/lib/states";
import type {
  BrokerDealerListResponse,
  BrokerDealerProfileResponse,
  ExecutiveContactItem,
} from "@/lib/types";

// Sprint 6 task #29: workspace-aware breadcrumb labels keyed by the
// `source` param threaded through the return envelope so the detail
// page can render "Back to My Favorites" / "Back to Visited Firms"
// instead of always saying "Back to Master List".
const SOURCE_LABELS: Record<DetailSource, { breadcrumb: string; back: string }> = {
  "master-list": { breadcrumb: "Master List", back: "Back to Master List" },
  favorites: { breadcrumb: "My Favorites", back: "Back to My Favorites" },
  visited: { breadcrumb: "Visited Firms", back: "Back to Visited Firms" },
};

// Cap the favorites / visits walker fetch at the BE's max page size.
// Typical users have < 100 entries so a single round trip resolves
// prev/next; users with > 100 fall through to /adjacent (same fallback
// as a no-envelope deep-link). Cross-page walking is omitted on
// purpose — see plans/fe-favorites-visited-sort-2026-04-29.md.
const USER_LIST_WALKER_LIMIT = 100;

// Shared button presets — kept as constants so the page can stay focused on
// composition rather than re-typing the same Tailwind utility chains.
const PRIMARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60";
const SECONDARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-4 py-2 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45";

// Builds the same /api/v1/broker-dealers query the master list emits,
// from a recovered MasterListQueryState. Mirrors the queryPath useMemo
// in master-list-workspace-client.tsx so the two callers stay in lock
// step — Next Lead must walk the *exact* same result set the user was
// looking at when they clicked into the firm.
function listPathFromReturnState(
  state: MasterListQueryState,
  pageOverride?: number,
): string {
  return buildApiPath("/api/v1/broker-dealers", {
    search: state.search,
    state: state.state
      ? [stateCodeFromName(state.state) ?? state.state]
      : undefined,
    health: state.health === "All" ? undefined : [state.health],
    lead_priority:
      state.leadPriority === "All" ? undefined : [state.leadPriority],
    clearing_partner: state.clearingPartner ? [state.clearingPartner] : undefined,
    clearing_type:
      state.clearingType === "All" ? undefined : [state.clearingType],
    types_of_business:
      state.typesOfBusiness.length > 0 ? state.typesOfBusiness : undefined,
    min_net_capital: state.minNetCapital ?? undefined,
    max_net_capital: state.maxNetCapital ?? undefined,
    registered_after: state.registeredAfter ?? undefined,
    registered_before: state.registeredBefore ?? undefined,
    list: state.list,
    sort_by: state.sortBy,
    sort_dir: state.sortDir,
    page: pageOverride ?? state.page,
    limit: state.limit,
  });
}

export function BrokerDealerDetailClient({ brokerDealerId }: { brokerDealerId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();

  // The master-list workspace appends ?return=<encoded-url> to every
  // row link (see master-list-workspace-client.tsx). When present, it's
  // the source of truth for the user's filtered/sorted/paginated view.
  // When absent (deep-link, bookmark, direct visit), Next Lead falls
  // back to the global /adjacent endpoint so the button still works.
  const returnRaw = searchParams.get("return");
  const returnState = useMemo(
    () => parseReturnParam(returnRaw),
    [returnRaw],
  );
  const returnEnvelope = useMemo(
    () => (returnState ? encodeReturnParam(returnState) : ""),
    [returnState],
  );
  // Source-aware back-link href + copy. Defaults to /master-list when
  // there's no return envelope so deep-link visits keep their previous
  // breadcrumb behaviour.
  const sourceListHref = useMemo<Route>(
    () =>
      returnState
        ? (buildSourceListUrl(returnState) as Route)
        : "/master-list",
    [returnState],
  );
  const sourceLabels = useMemo(
    () => SOURCE_LABELS[returnState?.source ?? "master-list"],
    [returnState?.source],
  );

  const [profile, setProfile] = useState<BrokerDealerProfileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [isEnriching, setIsEnriching] = useState(false);
  const [attemptedAutoEnrich, setAttemptedAutoEnrich] = useState(false);
  const [isHealthChecking, setIsHealthChecking] = useState(false);
  const [healthCheckResult, setHealthCheckResult] = useState<string | null>(null);
  const [prevId, setPrevId] = useState<number | null>(null);
  const [nextId, setNextId] = useState<number | null>(null);

  // Resolve adjacent firm IDs.
  //
  // With a `return` envelope, fetch the same filtered/sorted page the
  // user came from, locate the current firm by id, and step ±1. When
  // the firm sits at a page boundary, fetch the neighbouring page to
  // surface the cross-page neighbour. No wrap at the head/tail of the
  // result set — the button disables.
  //
  // Without an envelope, fall back to the existing /adjacent endpoint
  // so deep-link visits keep their previous behaviour.
  useEffect(() => {
    let active = true;
    const numericId = Number(brokerDealerId);

    async function resolveFromReturnState(state: MasterListQueryState) {
      const response = await apiRequest<BrokerDealerListResponse>(
        listPathFromReturnState(state),
      );
      if (!active) return;

      const idx = response.items.findIndex((item) => item.id === numericId);
      if (idx === -1) {
        // The firm dropped out of the user's view between the master
        // list and now (data refresh, filter narrowing, etc.). Fall
        // back to the global walker so the buttons still navigate.
        await resolveFromAdjacent();
        return;
      }

      let prev: number | null = null;
      let next: number | null = null;

      if (idx > 0) {
        prev = response.items[idx - 1].id;
      } else if (response.meta.page > 1) {
        const prevPage = await apiRequest<BrokerDealerListResponse>(
          listPathFromReturnState(state, response.meta.page - 1),
        );
        if (!active) return;
        if (prevPage.items.length > 0) {
          prev = prevPage.items[prevPage.items.length - 1].id;
        }
      }

      if (idx < response.items.length - 1) {
        next = response.items[idx + 1].id;
      } else if (response.meta.page < response.meta.total_pages) {
        const nextPage = await apiRequest<BrokerDealerListResponse>(
          listPathFromReturnState(state, response.meta.page + 1),
        );
        if (!active) return;
        if (nextPage.items.length > 0) {
          next = nextPage.items[0].id;
        }
      }

      setPrevId(prev);
      setNextId(next);
    }

    async function resolveFromAdjacent() {
      try {
        const adj = await apiRequest<{
          prev_id: number | null;
          next_id: number | null;
        }>(`/api/v1/broker-dealers/${brokerDealerId}/adjacent`);
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

    // Sprint 6 task #29: walk the user's favorites or visit history
    // when the source envelope says so. The BE pins the sort
    // (created_at DESC for favorites, last_visited_at DESC for
    // visits) so a single limit=100 fetch covers the typical user.
    // Past 100, we degrade to the global /adjacent walker — same
    // fallback as a deep-link visit.
    async function resolveFromUserList(
      source: Exclude<DetailSource, "master-list">,
    ) {
      const path = source === "favorites" ? "/api/v1/favorites" : "/api/v1/visits";
      const response = await apiRequest<FavoriteListResponse | VisitListResponse>(
        buildApiPath(path, {
          limit: USER_LIST_WALKER_LIMIT,
          offset: 0,
        }),
      );
      if (!active) return;

      const items = response.items;
      const idx = items.findIndex((item) => item.id === numericId);
      if (idx === -1) {
        await resolveFromAdjacent();
        return;
      }
      setPrevId(idx > 0 ? items[idx - 1].id : null);
      setNextId(idx < items.length - 1 ? items[idx + 1].id : null);
    }

    if (returnState && Number.isFinite(numericId)) {
      if (returnState.source === "favorites" || returnState.source === "visited") {
        void resolveFromUserList(returnState.source).catch(() => {
          if (active) void resolveFromAdjacent();
        });
      } else {
        void resolveFromReturnState(returnState).catch(() => {
          if (active) void resolveFromAdjacent();
        });
      }
    } else {
      void resolveFromAdjacent();
    }

    return () => {
      active = false;
    };
  }, [brokerDealerId, returnState]);

  // Build a same-shape /master-list/{id} link that preserves the same
  // return envelope so chaining Next Lead doesn't lose the master-list
  // context after the first click.
  const buildAdjacentHref = useCallback(
    (id: number): Route => {
      const base = `/master-list/${id}`;
      return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
    },
    [returnEnvelope],
  );

  // Fire-and-forget visit tracking. The backend upserts on (user_id, bd_id)
  // so a failure here never blocks render or mutates the detail payload.
  useEffect(() => {
    const numericId = Number(brokerDealerId);
    if (!Number.isFinite(numericId)) return;
    recordVisit(numericId).catch(() => {
      /* swallow — visit history is non-critical */
    });
  }, [brokerDealerId]);

  const reloadProfile = useCallback(async () => {
    const response = await apiRequest<BrokerDealerProfileResponse>(
      `/api/v1/broker-dealers/${brokerDealerId}/profile`,
    );
    setProfile(response);
  }, [brokerDealerId]);

  useEffect(() => {
    let active = true;
    async function loadProfile() {
      try {
        const response = await apiRequest<BrokerDealerProfileResponse>(
          `/api/v1/broker-dealers/${brokerDealerId}/profile`,
        );
        if (active) setProfile(response);
      } catch (loadError) {
        if (active) {
          setError(
            loadError instanceof Error ? loadError.message : "Unable to load broker-dealer profile.",
          );
        }
      }
    }
    void loadProfile();
    return () => {
      active = false;
    };
  }, [brokerDealerId]);

  async function runHealthCheck() {
    setIsHealthChecking(true);
    setHealthCheckResult(null);
    try {
      const result = await apiRequest<{ total_changes: number; fields_refreshed: string[] }>(
        `/api/v1/broker-dealers/${brokerDealerId}/health-check`,
        { method: "POST" },
      );
      if (result.total_changes > 0) {
        setHealthCheckResult(
          `Updated ${result.total_changes} field(s): ${result.fields_refreshed.join(", ")}`,
        );
        await reloadProfile();
      } else {
        setHealthCheckResult("All data is up to date.");
      }
    } catch (err) {
      setHealthCheckResult(err instanceof Error ? err.message : "Health check failed.");
    } finally {
      setIsHealthChecking(false);
    }
  }

  async function enrichContacts() {
    setIsEnriching(true);
    setEnrichError(null);
    try {
      const directOwners = profile?.broker_dealer.direct_owners ?? [];
      const executiveOfficers = profile?.broker_dealer.executive_officers ?? [];
      const officers = dedupOfficers([
        ...directOwners.map(toOfficerEntity),
        ...executiveOfficers.map(toOfficerEntity),
      ]);
      const contacts = await apiRequest<BrokerDealerProfileResponse["executive_contacts"]>(
        `/api/v1/broker-dealers/${brokerDealerId}/enrich`,
        { method: "POST", body: JSON.stringify({ officers }) },
      );
      setProfile((c) => (c ? { ...c, executive_contacts: contacts } : c));
    } catch (err) {
      setEnrichError(err instanceof Error ? err.message : "Unable to enrich contacts.");
    } finally {
      setIsEnriching(false);
    }
  }

  useEffect(() => {
    if (!profile || profile.executive_contacts.length > 0 || attemptedAutoEnrich || isEnriching) return;
    setAttemptedAutoEnrich(true);
    void enrichContacts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attemptedAutoEnrich, isEnriching, profile]);

  const chartPoints = useMemo(() => {
    if (!profile) return [] as Array<{ label: string; value: number }>;
    return profile.financials
      .slice()
      .reverse()
      .map((item) => ({
        label: new Date(item.report_date).getFullYear().toString(),
        value: item.net_capital,
      }));
  }, [profile]);

  if (error) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </div>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div
          className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8"
          style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
        >
          <div className="h-6 w-56 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-4 h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-8 grid gap-4 xl:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  const { broker_dealer: bd } = profile;
  const location = [bd.city, bd.state].filter(Boolean).join(", ");
  const websiteDomain = bd.website
    ? bd.website.replace(/^https?:\/\//i, "").replace(/\/+$/, "").split("/")[0]?.toLowerCase() ?? null
    : null;
  const contactEmail = profile.executive_contacts.find((c) => c.email)?.email ?? null;
  const emailDomain = contactEmail ? contactEmail.split("@")[1]?.toLowerCase() ?? null : null;
  const resolvedDomain = websiteDomain || emailDomain;
  const classification = classificationDisplay(bd.clearing_classification);

  // Derived contact-matching for the People panel
  const finraNames = [
    ...(bd.direct_owners ?? []).map((o) => o.name),
    ...(bd.executive_officers ?? []).map((o) => o.name),
  ];
  const matchedContactIds = new Set<number>();
  for (const contact of profile.executive_contacts) {
    if (finraNames.some((n) => nameMatches(n, contact))) {
      matchedContactIds.add(contact.id);
    }
  }
  const matchForFinra = (finraDisplay: string): ExecutiveContactItem | undefined =>
    profile.executive_contacts.find((c) => nameMatches(finraDisplay, c));
  const additionalContacts = profile.executive_contacts.filter(
    (c) => !matchedContactIds.has(c.id),
  );

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* ── Topbar: breadcrumbs + h1 + meta + right rail ── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={sourceListHref}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              {sourceLabels.breadcrumb}
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Firm Detail
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
              {bd.name}
            </h1>
            <ListPicker
              firmId={bd.id}
              variant="detail"
              initialDefaultMember={profile.is_favorited}
            />
            {isFirmIncomplete(bd) ? (
              <RefreshFirmButton firmId={bd.id} />
            ) : null}
          </div>
          <FirmWebsiteLink firmId={bd.id} firmName={bd.name} website={bd.website} />
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <span>
              CIK <span className="font-mono text-[var(--text-dim,#475569)]">{bd.cik ?? "N/A"}</span>
            </span>
            <span aria-hidden>·</span>
            <span>
              CRD{" "}
              <span className="font-mono text-[var(--text-dim,#475569)]">
                {bd.crd_number ?? "Pending"}
              </span>
            </span>
            <span aria-hidden>·</span>
            <span>{location || "Location unknown"}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <button
            type="button"
            onClick={() => void runHealthCheck()}
            disabled={isHealthChecking}
            className={SECONDARY_BTN}
          >
            {isHealthChecking ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.5} />
            ) : (
              <RefreshCw className="h-4 w-4" strokeWidth={2} />
            )}
            {isHealthChecking ? "Checking…" : "Health Check"}
          </button>
        </div>
      </div>

      {/* ── Status pills row + health-check inline message ── */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <Pill variant={healthVariant(bd.health_status)}>{healthLabel(bd.health_status)}</Pill>
        <span className="inline-flex items-center gap-1">
          <Pill variant={clearingTypeVariant(bd.current_clearing_type)}>
            {clearingTypeLabel(bd.current_clearing_type)}
          </Pill>
          {bd.current_clearing_type === null &&
          bd.current_clearing_unknown_reason ? (
            <UnknownCell
              reason={bd.current_clearing_unknown_reason}
              fallback={null}
              compact
            />
          ) : null}
        </span>
        {bd.current_clearing_is_competitor ? <Pill variant="competitor">COMPETITOR</Pill> : null}
        {classification ? (
          <Pill variant={classification.variant}>{classification.label}</Pill>
        ) : null}
        {bd.is_niche_restricted ? <Pill variant="warning">Niche / Restricted</Pill> : null}
        {bd.lead_priority ? (
          <Pill variant={priorityVariant(bd.lead_priority)}>
            {priorityLabel(bd.lead_priority)}
            {bd.lead_score !== null ? ` · ${bd.lead_score.toFixed(0)}` : ""}
          </Pill>
        ) : null}
        {healthCheckResult ? (
          <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">{healthCheckResult}</span>
        ) : null}
      </div>

      {/* ── Adjacent-firm nav ── */}
      <div className="mb-5 flex items-center justify-between gap-3">
        <button
          type="button"
          disabled={!prevId}
          onClick={() => prevId && router.push(buildAdjacentHref(prevId))}
          className={SECONDARY_BTN}
        >
          <ArrowLeft className="h-4 w-4" strokeWidth={2} />
          Previous Lead
        </button>
        <Link
          href={sourceListHref}
          className="text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          {sourceLabels.back}
        </Link>
        <button
          type="button"
          disabled={!nextId}
          onClick={() => nextId && router.push(buildAdjacentHref(nextId))}
          className={SECONDARY_BTN}
        >
          Next Lead
          <ArrowRight className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>

      {/* ── 2-column section grid ── */}
      <div className="grid gap-4 xl:grid-cols-2">
        {/* Financials */}
        <SectionPanel eyebrow="Financials" title="Net capital and trend">
          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <MiniStat
              label="Net Capital"
              value={
                bd.latest_net_capital !== null ? (
                  formatCurrency(bd.latest_net_capital)
                ) : (
                  <UnknownCell
                    reason={bd.financial_unknown_reason}
                    fallback="N/A"
                  />
                )
              }
            />
            <MiniStat
              label="Excess Capital"
              value={
                bd.latest_excess_net_capital !== null ? (
                  formatCurrency(bd.latest_excess_net_capital)
                ) : (
                  <UnknownCell
                    reason={bd.financial_unknown_reason}
                    fallback="N/A"
                  />
                )
              }
            />
            <MiniStat
              label="YoY Growth"
              value={
                bd.yoy_growth !== null ? (
                  formatPercent(bd.yoy_growth)
                ) : (
                  <UnknownCell
                    reason={bd.financial_unknown_reason}
                    fallback="N/A"
                  />
                )
              }
              valueClassName={
                bd.yoy_growth === null
                  ? "text-[var(--text-muted,#94a3b8)]"
                  : bd.yoy_growth >= 0
                  ? "text-[#16a34a]"
                  : "text-[var(--pill-red-text,#b91c1c)]"
              }
              helper={
                bd.yoy_growth === null && !bd.financial_unknown_reason
                  ? "Requires 2+ years of data"
                  : undefined
              }
            />
          </div>
          <FinancialTrendChart points={chartPoints} />
        </SectionPanel>

        {/* Assessment */}
        <SectionPanel eyebrow="Assessment" title="Firm profile overview">
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Registration Status"
              value={profile.registration_compliance.registration_status || "Not available"}
              compact
            />
            <MiniStat
              label="Registration Date"
              value={formatDate(profile.registration_compliance.registration_date)}
              compact
            />
            <MiniStat label="Address" value={location || "Not available"} compact />
            <MiniStat
              label="Branch Count"
              value={
                profile.registration_compliance.branch_count !== null
                  ? String(profile.registration_compliance.branch_count)
                  : "Not available"
              }
              compact
            />
          </div>

          {/* Types of Business */}
          <div className="mt-4">
            <div className="flex items-center gap-2">
              <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">Types of Business</p>
              {bd.types_of_business_total ? (
                <Pill variant="info">{bd.types_of_business_total} types</Pill>
              ) : null}
            </div>
            {bd.types_of_business && bd.types_of_business.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {bd.types_of_business.map((type) => (
                  <span
                    key={type}
                    className="rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1 text-[11px] text-[var(--text-dim,#475569)]"
                  >
                    {type}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">Not available</p>
            )}
            {bd.types_of_business_other ? (
              <div className="mt-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  Other Business Activities
                </p>
                <p className="mt-1">{bd.types_of_business_other}</p>
              </div>
            ) : null}
          </div>

          {/* PDF + Find emails action strip */}
          <div className="mt-4 flex flex-wrap items-start gap-2">
            <a
              href={`/api/backend/api/v1/broker-dealers/${brokerDealerId}/focus-report.pdf`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <Download className="h-3.5 w-3.5" strokeWidth={2} />
              FOCUS report (PDF)
            </a>
            {bd.crd_number ? (
              <a
                href={`/api/backend/api/v1/broker-dealers/${brokerDealerId}/brokercheck.pdf`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
              >
                <Download className="h-3.5 w-3.5" strokeWidth={2} />
                FINRA BrokerCheck (PDF)
              </a>
            ) : null}
            <FindEmailsButton brokerDealerId={brokerDealerId} resolvedDomain={resolvedDomain} />
          </div>

          <FocusReportSection brokerDealerId={brokerDealerId} onProfileRefresh={reloadProfile} />
        </SectionPanel>

        {/* People */}
        <SectionPanel
          eyebrow="People"
          title="Owners, officers, and contacts"
          headerAction={
            <button
              type="button"
              onClick={() => void enrichContacts()}
              disabled={isEnriching}
              className={PRIMARY_BTN}
            >
              {isEnriching ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.5} />
              ) : (
                <Sparkles className="h-4 w-4" strokeWidth={2} />
              )}
              {isEnriching ? "Generating…" : "Generate More Details"}
            </button>
          }
        >
          {enrichError ? (
            <div className="mb-3 rounded-2xl border border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.08)] px-4 py-3 text-sm text-[var(--pill-amber-text,#b45309)]">
              {enrichError}
            </div>
          ) : null}

          {bd.direct_owners && bd.direct_owners.length > 0 ? (
            <PeopleSubGroup title="Direct Owners">
              {bd.direct_owners.map((owner, i) => (
                <PersonCard
                  key={`owner-${i}`}
                  name={owner.name}
                  title={owner.title}
                  extra={owner.ownership_pct ? `Ownership: ${owner.ownership_pct}` : null}
                  contact={matchForFinra(owner.name)}
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {bd.executive_officers && bd.executive_officers.length > 0 ? (
            <PeopleSubGroup title="Executive Officers">
              {bd.executive_officers.map((officer, i) => (
                <PersonCard
                  key={`officer-${i}`}
                  name={officer.name}
                  title={officer.title}
                  contact={matchForFinra(officer.name)}
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {additionalContacts.length > 0 ? (
            <PeopleSubGroup title="Additional contacts">
              {additionalContacts.map((contact) => (
                <PersonCard
                  key={`contact-${contact.id}`}
                  name={contact.name}
                  title={contact.title}
                  contact={contact}
                  source={`${contact.source} · ${formatDate(contact.enriched_at)}`}
                />
              ))}
            </PeopleSubGroup>
          ) : null}
        </SectionPanel>

        {/* Relationship */}
        <SectionPanel eyebrow="Relationship" title="Clearing and introducing mapping">
          <div className="mb-4 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4 text-sm text-[var(--text-dim,#475569)]">
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">Clearing Arrangements</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {classification ? (
                <Pill variant={classification.variant}>{classification.label}</Pill>
              ) : (
                <span className="text-[var(--text-muted,#94a3b8)]">Not yet classified</span>
              )}
            </div>
            {bd.clearing_classification === "introducing" && bd.current_clearing_partner ? (
              <p className="mt-2">
                Clearing through:{" "}
                <span className="font-semibold text-[var(--text,#0f172a)]">
                  {bd.current_clearing_partner}
                </span>
              </p>
            ) : null}
            {(!bd.clearing_classification || bd.clearing_classification === "unknown") &&
            bd.clearing_raw_text ? (
              <div className="mt-3 rounded-2xl border border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.08)] px-4 py-3 text-xs text-[var(--pill-amber-text,#b45309)]">
                <p className="font-semibold">Raw clearing text (classification pending):</p>
                <p className="mt-1 leading-5">{bd.clearing_raw_text}</p>
              </div>
            ) : null}
            {bd.firm_operations_text &&
            bd.clearing_classification &&
            bd.clearing_classification !== "unknown" ? (
              <p className="mt-2 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
                {bd.firm_operations_text}
              </p>
            ) : null}
          </div>

          {profile.introducing_arrangements.length > 0 ? (
            <div className="mb-4">
              <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                Introducing Arrangements
              </p>
              <div className="mt-2 space-y-2">
                {profile.introducing_arrangements.map((arr) => {
                  const parsed = parseArrangementBlob(
                    [arr.statement, arr.description].filter(Boolean).join(" "),
                  );
                  const name = arr.business_name || parsed.partnerName;
                  const effective = arr.effective_date
                    ? formatDate(arr.effective_date)
                    : parsed.effectiveDate;
                  return (
                    <div
                      key={arr.id}
                      className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                    >
                      {name ? <p className="font-semibold text-[var(--text,#0f172a)]">{name}</p> : null}
                      <ArrangementFields
                        crd={parsed.partnerCrd}
                        address={parsed.partnerAddress}
                        effective={effective}
                        details={parsed.description || parsed.intro}
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {profile.industry_arrangements.length > 0 ? (
            <div className="mb-4">
              <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                Industry Arrangements
              </p>
              <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
                Determines whether the firm is truly self-clearing or relies on a third party.
              </p>
              <div className="mt-2 space-y-2">
                {profile.industry_arrangements.map((arr) => {
                  const kindLabel =
                    arr.kind === "books_records"
                      ? "Books / records"
                      : arr.kind === "accounts_funds"
                      ? "Accounts, funds, or securities"
                      : "Customer accounts, funds, or securities";
                  const parsed = arr.has_arrangement ? parseArrangementBlob(arr.description) : null;
                  return (
                    <div
                      key={arr.id}
                      className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-[var(--text,#0f172a)]">{kindLabel}</p>
                        <Pill variant={arr.has_arrangement ? "warning" : "healthy"}>
                          {arr.has_arrangement
                            ? "Maintained by a third party"
                            : "Not maintained by a third party"}
                        </Pill>
                      </div>
                      {arr.has_arrangement && parsed ? (
                        <div className="mt-3 space-y-2 text-sm text-[var(--text-dim,#475569)]">
                          {arr.partner_name || parsed.partnerName ? (
                            <p>
                              <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                                Partner
                              </span>{" "}
                              <span className="font-semibold text-[var(--text,#0f172a)]">
                                {arr.partner_name || parsed.partnerName}
                              </span>
                              {arr.partner_crd || parsed.partnerCrd ? (
                                <span className="ml-2 text-xs text-[var(--text-muted,#94a3b8)]">
                                  CRD #{arr.partner_crd || parsed.partnerCrd}
                                </span>
                              ) : null}
                            </p>
                          ) : null}
                          <ArrangementFields
                            crd={
                              arr.partner_name || parsed.partnerName
                                ? null
                                : arr.partner_crd || parsed.partnerCrd
                            }
                            address={arr.partner_address || parsed.partnerAddress}
                            effective={
                              arr.effective_date ? formatDate(arr.effective_date) : parsed.effectiveDate
                            }
                            details={parsed.description || parsed.intro}
                          />
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          <div
            className={`mb-4 rounded-2xl px-4 py-4 text-sm ${
              profile.deficiency_status.is_deficient
                ? "bg-[rgba(239,68,68,0.08)] text-[var(--pill-red-text,#b91c1c)]"
                : "bg-[rgba(16,185,129,0.08)] text-[var(--pill-green-text,#047857)]"
            }`}
          >
            <p className="font-semibold">
              {profile.deficiency_status.is_deficient
                ? "Deficiency notice active"
                : "No active deficiency notice"}
            </p>
            <p className="mt-2 leading-6">{profile.deficiency_status.message}</p>
          </div>

          <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">Clearing History</p>
          <div className="mt-2 space-y-2">
            {profile.clearing_arrangements.length === 0 ? (
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                No clearing history available yet.
              </div>
            ) : (
              profile.clearing_arrangements.map((item) => (
                <div
                  key={item.id}
                  className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
                        {item.clearing_partner ?? (
                          <UnknownCell
                            reason={item.unknown_reason}
                            fallback="Unknown partner"
                          />
                        )}
                      </p>
                      <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
                        Year {item.filing_year}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Pill variant={clearingTypeVariant(item.clearing_type)}>
                        {clearingTypeLabel(item.clearing_type)}
                      </Pill>
                      {item.clearing_type === null && item.unknown_reason ? (
                        <UnknownCell
                          reason={item.unknown_reason}
                          fallback={null}
                          compact
                        />
                      ) : null}
                      {item.is_competitor ? <Pill variant="competitor">COMPETITOR</Pill> : null}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </SectionPanel>
      </div>

      {/* ── Filing history (full width) ── */}
      <div className="mt-4">
        <SectionPanel eyebrow="Filing History" title="Chronological filing timeline">
          <div className="space-y-3">
            {profile.filing_history.length === 0 ? (
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
                No filing history is available yet.
              </div>
            ) : (
              profile.filing_history.map((item, index) => (
                <div
                  key={`${item.label}-${index}`}
                  className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-[var(--text,#0f172a)]">{item.label}</p>
                      <p className="mt-1 text-sm text-[var(--text-dim,#475569)]">{item.summary}</p>
                    </div>
                    {item.priority ? <AlertPriorityBadge priority={item.priority} /> : null}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-4 text-sm text-[var(--text-muted,#94a3b8)]">
                    <span>{formatDate(item.filed_at)}</span>
                    {item.source_filing_url ? (
                      <a
                        href={viewableFilingUrl(item.source_filing_url) ?? item.source_filing_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-[var(--accent,#6366f1)] hover:underline"
                      >
                        Open filing
                        <ExternalLink className="h-3 w-3" strokeWidth={2} />
                      </a>
                    ) : null}
                  </div>
                </div>
              ))
            )}
          </div>
        </SectionPanel>
      </div>
    </div>
  );
}

// Inline mini-stat card. Used inside the Financials and Assessment panels for
// the small key-value tiles. Compact mode tightens the vertical rhythm so the
// 4-up Assessment grid keeps its footprint.
function MiniStat({
  label,
  value,
  helper,
  valueClassName,
  compact,
}: {
  label: string;
  // Widened from `string` so callers can render a custom node (e.g. an
  // UnknownCell with hover-explained reason) when the underlying value
  // is null.
  value: React.ReactNode;
  helper?: string;
  valueClassName?: string;
  compact?: boolean;
}) {
  return (
    <div className={`rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 ${compact ? "py-3" : "py-4"} text-sm`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p
        className={`mt-1 ${
          compact
            ? "text-[13px] text-[var(--text,#0f172a)]"
            : "text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]"
        } ${valueClassName ?? ""}`}
      >
        {value}
      </p>
      {helper ? <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">{helper}</p> : null}
    </div>
  );
}

// Sub-group wrapper inside the People panel so each list (direct owners,
// executive officers, additional contacts) has the same heading + spacing.
function PeopleSubGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 last:mb-0">
      <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">{title}</p>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

// Single owner / officer / contact row used by every PeopleSubGroup. Keeps
// the Apollo source + enriched_at footer when present.
function PersonCard({
  name,
  title,
  extra,
  contact,
  source,
}: {
  name: string;
  title: string;
  extra?: string | null;
  contact?: ExecutiveContactItem;
  source?: string;
}) {
  return (
    <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
      <p className="flex flex-wrap items-center font-semibold text-[var(--text,#0f172a)]">
        <span>{name}</span>
        {contact ? <SourceBadge source={contact.source} /> : null}
      </p>
      {title ? <p className="mt-1">{title}</p> : null}
      {extra ? <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">{extra}</p> : null}
      {contact ? <ContactRow contact={contact} /> : null}
      {source ? (
        <p className="mt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
          {source}
        </p>
      ) : null}
    </div>
  );
}
