"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Globe,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";

import {
  apiRequest,
  buildApiPath,
  gapFillAdvisorContacts,
  getPipelineRunStatus,
  refreshAdvisor,
} from "@/lib/api";
import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import { DetailPageSkeleton } from "@/components/ui/detail-page-skeleton";
import {
  buildAdvisorListUrl,
  encodeReturnParam,
  parseReturnParam,
  ADVISOR_LIST_STATE_DEFAULTS,
  type AdvisorListQueryState,
} from "@/lib/advisor-list-state";
import { formatCurrency, formatDate } from "@/lib/format";
import { EmailScansSection } from "@/components/email-extractor/email-scans-section";
import { EMAIL_EXTRACTION_ENABLED } from "@/lib/feature-flags";
import {
  listScansForEntity,
  pickHydratableScan,
  SCAN_HYDRATION_LIMIT,
} from "@/lib/email-extractor";
import { ListPicker } from "@/components/list-picker/list-picker";
import { OutreachButton } from "@/components/master-list/outreach-button";
import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { PeopleTable } from "@/components/master-list/detail/people-table";
import { Pill } from "@/components/ui/pill";
import { agencyLabel } from "@/components/master-list/detail/clearing-membership-helpers";
import { SectionPanel } from "@/components/ui/section-panel";
import { CollapsiblePillList } from "@/components/ui/collapsible-pill-list";
import type {
  InvestmentAdvisorListResponse,
  InvestmentAdvisorProfileResponse,
} from "@/lib/types";

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

  // Manual refresh button state. The advisor detail page used to POST
  // /refresh-all on every mount; the bulk gap-fill runner now pre-fills
  // every 13F-filer advisor row, so the page is a pure DB read by
  // default. The Refresh button below remains as the escape hatch for
  // forcing the per-firm orchestrator when an operator wants
  // fresh-from-source data.
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  // Inline "Discovered Emails" section state. Mirrors the BD detail page:
  // `currentScanId` is the single source of truth, seeded from `?scanId=`
  // (deep-link / post-Find-Emails refresh) or the most-recent scan for
  // this advisor on first load. See hydration + URL-sync effects below.
  const [currentScanId, setCurrentScanId] = useState<number | null>(null);
  const [isHydratingScan, setIsHydratingScan] = useState(true);

  // Gap-fill button state. Distinct from Refresh because gap-fill only
  // targets advisor_contacts rows missing channels and is gated by a 30-day
  // cooldown — the 429 surfaces via gapFillError.
  const [isGapFilling, setIsGapFilling] = useState(false);
  const [gapFillError, setGapFillError] = useState<string | null>(null);
  const [gapFillNotice, setGapFillNotice] = useState<string | null>(null);
  // Apollo phone reveals land asynchronously (~1-3 min after a gap-fill run);
  // this timer drives a few extra /profile re-fetches so they appear without a
  // manual reload. Cleared on advisor switch / unmount.
  const latePhonePollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Filings list renders collapsed to the most-recent few; "Show all"
  // expands the full in-memory list. Reset when switching advisors (the
  // component instance persists across Prev/Next — only advisorId changes).
  const [showAllFilings, setShowAllFilings] = useState(false);

  // Fetch /profile immediately on mount. Errors surface via setError so
  // a failed first load shows the error page. `reloadProfile` is the
  // re-fetch helper the manual Refresh button calls; it lets the caller
  // decide what to do with any error (the button swallows transients
  // into a notice rather than blanking the page).
  const reloadProfile = useCallback(async () => {
    const response = await apiRequest<InvestmentAdvisorProfileResponse>(
      `/api/v1/investment-advisors/${advisorId}/profile`,
    );
    setData(response);
    setError(null);
  }, [advisorId]);

  useEffect(() => {
    let active = true;
    async function loadProfile() {
      try {
        const response = await apiRequest<InvestmentAdvisorProfileResponse>(
          `/api/v1/investment-advisors/${advisorId}/profile`,
        );
        if (active) {
          setData(response);
          setError(null);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load advisor");
        }
      }
    }
    void loadProfile();
    return () => {
      active = false;
    };
  }, [advisorId]);

  // Collapse the filings list and clear any gap-fill notice/error whenever we
  // navigate to another advisor (the instance may persist across Prev/Next, so
  // reset rather than rely on remount).
  useEffect(() => {
    setShowAllFilings(false);
    setGapFillNotice(null);
    setGapFillError(null);
    return () => {
      if (latePhonePollRef.current) {
        clearTimeout(latePhonePollRef.current);
        latePhonePollRef.current = null;
      }
    };
  }, [advisorId]);

  // Manual refresh handler. POST /refresh-all on click, poll the parent
  // PipelineRun until terminal (180s deadline, matches the BD detail
  // page), then re-fetch /profile so the user picks up whatever fresh
  // data the orchestrator wrote. Errors surface via refreshError; a
  // transient failure leaves the stale view intact.
  const runManualRefresh = useCallback(async () => {
    const numericId = Number(advisorId);
    if (!Number.isFinite(numericId)) return;
    setIsRefreshing(true);
    setRefreshError(null);
    try {
      const result = await refreshAdvisor(numericId, "all");
      if (result.status !== "skipped" && result.run_id !== null) {
        const runId = result.run_id;
        const deadline = Date.now() + 180_000;
        const TERMINAL = new Set([
          "completed",
          "completed_with_errors",
          "failed",
        ]);
        while (Date.now() < deadline) {
          try {
            const detail = await getPipelineRunStatus(runId);
            if (TERMINAL.has(detail.status)) break;
          } catch {
            /* transient poll error — keep waiting */
          }
          await new Promise<void>((resolve) => setTimeout(resolve, 2000));
        }
      }
      await reloadProfile();
    } catch (err) {
      setRefreshError(err instanceof Error ? err.message : "Refresh failed.");
    } finally {
      setIsRefreshing(false);
    }
  }, [advisorId, reloadProfile]);

  // "Generate More Details" handler. POST /gap-fill-contacts with force (the
  // user-facing button always re-runs past the cost cooldown, matching the BD
  // enrich button), poll the run until terminal (300s deadline -- headroom so
  // the "Generating…" loader keeps spanning the run's optional final
  // web-fallback stage; the user just sees the same generic progress), then
  // reload /profile so the new emails/phones/LinkedIn URLs land in the icon
  // popovers. Error / notice strings show inline in the People panel.
  const runGapFill = useCallback(async () => {
    const numericId = Number(advisorId);
    if (!Number.isFinite(numericId)) return;
    setIsGapFilling(true);
    setGapFillError(null);
    setGapFillNotice(null);
    try {
      const result = await gapFillAdvisorContacts(numericId, true);
      if (result.run_id !== null) {
        const runId = result.run_id;
        const deadline = Date.now() + 300_000;
        const TERMINAL = new Set([
          "completed",
          "completed_with_errors",
          "failed",
        ]);
        while (Date.now() < deadline) {
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
                  /* notes wasn't JSON — fall back to the default. */
                }
              }
              setGapFillNotice(summary);
              break;
            }
          } catch {
            /* transient poll error — keep waiting */
          }
          await new Promise<void>((resolve) => setTimeout(resolve, 2000));
        }
      } else {
        setGapFillNotice(result.reason ?? "Gap-fill skipped.");
      }
      await reloadProfile();
      // Apollo phone reveals arrive async (~1-3 min) via the webhook; re-fetch
      // the profile a few more times so the numbers appear without a manual
      // reload. Non-blocking — the button is already done at this point.
      if (latePhonePollRef.current) clearTimeout(latePhonePollRef.current);
      let phonePollAttempts = 0;
      const pollLatePhones = () => {
        latePhonePollRef.current = setTimeout(() => {
          phonePollAttempts += 1;
          void reloadProfile().catch(() => {
            /* transient — keep polling */
          });
          if (phonePollAttempts < 5) {
            pollLatePhones();
          } else {
            latePhonePollRef.current = null;
          }
        }, 30_000);
      };
      pollLatePhones();
    } catch (err) {
      setGapFillError(
        err instanceof Error ? err.message : "Gap-fill failed.",
      );
    } finally {
      setIsGapFilling(false);
    }
  }, [advisorId, reloadProfile]);

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

  // Hydrate the inline "Discovered Emails" section. Two sources, in
  // precedence order:
  //   1. `?scanId=` in the URL — wins, so deep-links and post-Find-Emails
  //      refreshes re-open the same scan.
  //   2. Most-recent scan *with emails* for this advisor (via
  //      pickHydratableScan) — so the user sees prior emails on first load
  //      without re-running, and a newer empty/failed retry never masks an
  //      older run that has results.
  // Reads `window.location.search` directly so this effect doesn't have
  // to depend on the reactive `searchParams` (the URL-sync effect below
  // would otherwise feed back into this and re-fetch on every change).
  useEffect(() => {
    const numericId = Number(advisorId);
    if (!Number.isFinite(numericId)) {
      setIsHydratingScan(false);
      return;
    }

    const urlScanId = new URLSearchParams(window.location.search).get(
      "scanId",
    );
    if (urlScanId) {
      const parsed = Number(urlScanId);
      if (Number.isFinite(parsed)) {
        setCurrentScanId(parsed);
        setIsHydratingScan(false);
        return;
      }
    }

    let active = true;
    setIsHydratingScan(true);
    setCurrentScanId(null);
    listScansForEntity({ kind: "advisor", id: numericId }, SCAN_HYDRATION_LIMIT)
      .then((scans) => {
        if (!active) return;
        const preferred = pickHydratableScan(scans);
        if (preferred) {
          setCurrentScanId(preferred.id);
        }
      })
      .catch(() => {
        /* swallow — empty state will render */
      })
      .finally(() => {
        if (active) setIsHydratingScan(false);
      });

    return () => {
      active = false;
    };
  }, [advisorId]);

  // Persist the active scan in the URL so a refresh re-opens the same
  // scan inside the inline section. Guards against a feedback loop by
  // checking whether `?scanId=` already matches before replacing.
  useEffect(() => {
    if (currentScanId === null) return;
    const desired = String(currentScanId);
    if (searchParams.get("scanId") === desired) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("scanId", desired);
    router.replace(
      `/advisor-list/${advisorId}?${params.toString()}` as Route,
    );
  }, [currentScanId, advisorId, searchParams, router]);

  // Same-shape /advisor-list/{id} link that preserves the return envelope so
  // chaining Next/Previous keeps the user's filtered context.
  const buildAdjacentHref = (id: number): Route => {
    const base = `/advisor-list/${id}`;
    return (returnEnvelope ? `${base}?return=${returnEnvelope}` : base) as Route;
  };

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
    return <DetailPageSkeleton />;
  }

  const { advisor, contacts, filings } = data;
  const location = [advisor.city, advisor.state].filter(Boolean).join(", ");
  const directOwners = advisor.direct_owners ?? [];
  const indirectOwners = advisor.indirect_owners ?? [];
  const executiveOfficers = advisor.executive_officers ?? [];
  const advisoryActivities = advisor.advisory_activities ?? [];
  const clientTypes = advisor.client_types ?? [];
  const clientCounts = advisor.client_counts ?? null;
  // Filings render collapsed to the 6 most-recent; the rest expand on demand.
  const FILINGS_COLLAPSED = 6;
  const visibleFilings = showAllFilings
    ? filings
    : filings.slice(0, FILINGS_COLLAPSED);

  // Resolve the firm domain for the inline Find-emails section: prefer the
  // advisor's website, fall back to the first contact email's domain.
  // Advisor contacts carry a multi-value `emails[]` array (not the single
  // `email` field used on BD contacts) so the lookup walks both shapes.
  const websiteDomain = advisor.website
    ? advisor.website
        .replace(/^https?:\/\//i, "")
        .replace(/\/+$/, "")
        .split("/")[0]
        ?.toLowerCase() ?? null
    : null;
  const contactEmail =
    contacts.find((c) => (c.emails ?? [])[0]?.value)?.emails?.[0]?.value ??
    contacts.find((c) => c.email)?.email ??
    null;
  const emailDomain = contactEmail
    ? contactEmail.split("@")[1]?.toLowerCase() ?? null
    : null;
  const resolvedDomain = websiteDomain || emailDomain;

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
            {advisor.files_13f ? (
              <Pill variant="healthy">13F filer</Pill>
            ) : null}
            {advisor.member_agencies.map((code) => (
              <Pill key={code} variant="member">
                {agencyLabel(code)} member
              </Pill>
            ))}
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
        <div className="flex shrink-0 items-center gap-2.5">
          <Button
            type="button"
            variant="outline"
            onClick={() => void runManualRefresh()}
            disabled={isRefreshing || isGapFilling}
          >
            {isRefreshing ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.5} />
            ) : (
              <RefreshCw className="h-4 w-4" strokeWidth={2} />
            )}
            {isRefreshing ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </div>

      {refreshError ? (
        <div className="mb-3 rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-2.5 text-[13px] text-[var(--pill-red-text,#b91c1c)]">
          {refreshError}
        </div>
      ) : null}

      {/* ── Adjacent-advisor nav — mirrors the master-list detail page ── */}
      <div className="mb-5 flex items-center justify-between gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={!prevId}
          onClick={() => prevId && router.push(buildAdjacentHref(prevId))}
        >
          <ArrowLeft className="h-4 w-4" strokeWidth={2} />
          Previous
        </Button>
        <Link
          href={backHref}
          className="text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          Back to Investment Advisors
        </Link>
        <Button
          type="button"
          variant="outline"
          disabled={!nextId}
          onClick={() => nextId && router.push(buildAdjacentHref(nextId))}
        >
          Next
          <ArrowRight className="h-4 w-4" strokeWidth={2} />
        </Button>
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

      {/* ── 2-column section layout — independent flex columns (no height-locking) ───────────────────────────────────────── */}
      <div className="flex flex-col gap-4 xl:flex-row">
      <div className="flex min-w-0 flex-1 flex-col gap-4">
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

        {/* People */}
        <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-[var(--text-muted,#94a3b8)]">
              {isGapFilling
                ? "Discovering contacts across providers… this can take a moment."
                : "Discover additional executive contacts for this firm."}
            </p>
            <button
              type="button"
              onClick={() => void runGapFill()}
              disabled={isGapFilling || isRefreshing}
              className={clsx(
                buttonBase,
                buttonSizes.md,
                "shrink-0 bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] hover:brightness-110",
              )}
            >
              {isGapFilling ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.5} />
              ) : (
                <Sparkles className="h-4 w-4" strokeWidth={2} />
              )}
              {isGapFilling ? "Generating…" : "Generate More Details"}
            </button>
          </div>

          {gapFillError ? (
            <div className="mb-3 rounded-2xl border border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.08)] px-4 py-3 text-sm text-[var(--pill-amber-text,#b45309)]">
              {gapFillError}
            </div>
          ) : null}

          {gapFillNotice ? (
            <div className="mb-3 rounded-2xl border border-[rgba(59,130,246,0.25)] bg-[rgba(59,130,246,0.08)] px-4 py-3 text-sm text-[var(--pill-blue-text,#1d4ed8)]">
              {gapFillNotice}
            </div>
          ) : null}

          {directOwners.length === 0 &&
          executiveOfficers.length === 0 &&
          indirectOwners.length === 0 &&
          contacts.length === 0 ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No owners, officers, or contacts on file yet.
            </p>
          ) : null}

          {directOwners.length > 0 ? (
            <PeopleTable
              title="Direct owners"
              items={directOwners}
              columns={[
                {
                  header: "Name",
                  cell: (o) => (
                    <span className="font-semibold text-[var(--text,#0f172a)]">
                      {o.name ?? "—"}
                    </span>
                  ),
                },
                { header: "Title", cell: (o) => o.title ?? "—" },
                {
                  header: "Ownership",
                  cell: (o) => o.ownership_pct ?? "—",
                  className: "whitespace-nowrap",
                },
              ]}
            />
          ) : null}

          {/* Executive officers — one merged table. When contacts are
              populated (enrichment chain has run), drive from advisor_contacts
              so names come out title-cased and the Channels/Outreach columns
              show. Pre-enrichment, fall back to the raw Form ADV officer
              list so the page still shows the roster. The two sources are
              the same 27 people for a fully-enriched advisor; the chain's
              names-only fallback guarantees parity even when no provider
              matched a given officer. */}
          {contacts.length > 0 ? (
            <PeopleTable
              title="Executive officers"
              items={contacts}
              columns={[
                {
                  header: "Name",
                  cell: (c) => (
                    <span className="font-semibold text-[var(--text,#0f172a)]">
                      {c.name}
                    </span>
                  ),
                },
                { header: "Title", cell: (c) => c.title ?? "—" },
                {
                  header: "Channels",
                  cell: (c) => <ChannelIconCell contact={c} />,
                  className: "whitespace-nowrap",
                },
                {
                  header: "Outreach",
                  cell: (c) =>
                    c.email || (c.emails?.length ?? 0) > 0 ? (
                      <OutreachButton
                        entityKind="advisor"
                        entityId={Number(advisorId)}
                        entityName={advisor.name}
                        contact={c}
                      />
                    ) : null,
                  className: "whitespace-nowrap",
                },
              ]}
            />
          ) : executiveOfficers.length > 0 ? (
            <PeopleTable
              title="Executive officers"
              items={executiveOfficers}
              columns={[
                {
                  header: "Name",
                  cell: (o) => (
                    <span className="font-semibold text-[var(--text,#0f172a)]">
                      {o.name ?? "—"}
                    </span>
                  ),
                },
                { header: "Title", cell: (o) => o.title ?? "—" },
              ]}
            />
          ) : null}

          {indirectOwners.length > 0 ? (
            <PeopleTable
              title="Indirect owners"
              items={indirectOwners}
              columns={[
                {
                  header: "Name",
                  cell: (o) => (
                    <span className="font-semibold text-[var(--text,#0f172a)]">
                      {o.name ?? "—"}
                    </span>
                  ),
                },
                { header: "Title", cell: (o) => o.title ?? "—" },
                {
                  header: "Ownership",
                  cell: (o) => o.ownership_pct ?? "—",
                  className: "whitespace-nowrap",
                },
              ]}
            />
          ) : null}
        </SectionPanel>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-4">
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
            {/* Formation date intentionally not rendered: Form ADV doesn't
                ask for it and the IAPD per-firm payload doesn't carry one,
                so the column would be "Not available" for nearly every IA.
                Field remains on the schema/model — re-enable if a corporate-
                records source (OpenCorporates / state SOS) is ever wired. */}
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

          {/* Alternative Names — Form ADV Schedule D §1.B "Other Business
              Names" from the IAPD per-firm payload (basicInformation.otherNames),
              filtered to drop the firm's own primary/legal name. Hidden when the
              firm has none so the card stays compact. */}
          {advisor.other_business_names && advisor.other_business_names.length > 0 ? (
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">Alternative Names</p>
                <Pill variant="info">{advisor.other_business_names.length} names</Pill>
              </div>
              <CollapsiblePillList items={advisor.other_business_names} />
            </div>
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
              {visibleFilings.map((filing) => (
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
              {filings.length > FILINGS_COLLAPSED ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setShowAllFilings((prev) => !prev)}
                >
                  {showAllFilings
                    ? "Show fewer"
                    : `Show all (${filings.length})`}
                </Button>
              ) : null}
            </div>
          )}
        </SectionPanel>
      </div>
      </div>

      {/* ── Discovered emails (full width) — hidden: no data extraction in-app ── */}
      {EMAIL_EXTRACTION_ENABLED && (
        <div className="mt-4">
          <EmailScansSection
            entityKind="advisor"
            entityId={Number(advisorId)}
            currentScanId={currentScanId}
            resolvedDomain={resolvedDomain}
            isHydrating={isHydratingScan}
            onScanCreated={setCurrentScanId}
          />
        </div>
      )}
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

