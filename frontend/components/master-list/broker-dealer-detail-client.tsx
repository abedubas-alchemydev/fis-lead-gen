"use client";

import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { AlertPriorityBadge } from "@/components/alerts/alert-priority-badge";
import { ClearingTypeBadge } from "@/components/master-list/clearing-type-badge";
import { CompetitorBadge } from "@/components/master-list/competitor-badge";
import { HealthBadge } from "@/components/master-list/health-badge";
import { LeadPriorityBadge } from "@/components/master-list/lead-priority-badge";
import { apiRequest } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type { BrokerDealerProfileResponse, FocusCeoExtractionResponse } from "@/lib/types";

/* ── Shared sub-components ─────────────────────────────────── */

function FinancialTrendChart({ points }: { points: Array<{ label: string; value: number }> }) {
  const viewBoxWidth = 360;
  const viewBoxHeight = 160;

  const path = useMemo(() => {
    if (points.length <= 1) return "";
    const values = points.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(max - min, 1);
    return points
      .map((p, i) => {
        const x = (i / Math.max(points.length - 1, 1)) * (viewBoxWidth - 30) + 15;
        const y = viewBoxHeight - (((p.value - min) / range) * (viewBoxHeight - 30) + 15);
        return `${i === 0 ? "M" : "L"} ${x} ${y}`;
      })
      .join(" ");
  }, [points]);

  if (points.length === 0) {
    return <div className="rounded-2xl bg-slate-50 px-4 py-10 text-sm text-slate-500">No financial history available yet.</div>;
  }

  // Single data point — show a value card instead of a chart line
  if (points.length === 1) {
    return (
      <div className="space-y-3">
        <div className="rounded-2xl bg-slate-50 px-4 py-6 text-center">
          <p className="text-sm text-slate-500">{points[0].label}</p>
          <p className="mt-2 text-2xl font-semibold text-navy">{formatCurrency(points[0].value)}</p>
          <p className="mt-2 text-xs text-slate-400">Only one reporting period available. The trend chart will appear when a second year of data is filed.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <svg viewBox={`0 0 ${viewBoxWidth} ${viewBoxHeight}`} className="w-full rounded-2xl bg-slate-50 p-3">
        <path d={path} fill="none" stroke="#1F5FA6" strokeWidth="4" strokeLinecap="round" />
        {/* Draw dots at each data point */}
        {points.map((p, i) => {
          const values = points.map((pt) => pt.value);
          const min = Math.min(...values);
          const max = Math.max(...values);
          const range = Math.max(max - min, 1);
          const x = (i / Math.max(points.length - 1, 1)) * (viewBoxWidth - 30) + 15;
          const y = viewBoxHeight - (((p.value - min) / range) * (viewBoxHeight - 30) + 15);
          return <circle key={p.label} cx={x} cy={y} r="5" fill="#1F5FA6" />;
        })}
      </svg>
      <div className="grid gap-2 sm:grid-cols-2">
        {points.map((p) => (
          <div key={p.label} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
            <p className="font-medium text-navy">{p.label}</p>
            <p className="mt-1">{formatCurrency(p.value)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function QuadrantCard({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <article className="rounded-[28px] border border-white/80 bg-white/92 p-6 shadow-shell">
      <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold text-navy">{title}</h2>
      <div className="mt-5">{children}</div>
    </article>
  );
}

function ClassificationBadge({ classification }: { classification: string | null }) {
  if (!classification || classification === "unknown") return null;
  const map: Record<string, { label: string; className: string }> = {
    true_self_clearing: { label: "True Self-Clearing", className: "bg-emerald-100 text-emerald-700" },
    introducing: { label: "Introducing", className: "bg-blue-100 text-blue-700" }
  };
  const config = map[classification];
  if (!config) return null;
  return <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${config.className}`}>{config.label}</span>;
}

function NicheRestrictedBadge({ isNiche }: { isNiche: boolean }) {
  if (!isNiche) return null;
  return <span className="inline-flex rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-700">Niche / Restricted</span>;
}

/* ── Main component ────────────────────────────────────────── */

export function BrokerDealerDetailClient({ brokerDealerId }: { brokerDealerId: string }) {
  const router = useRouter();
  const [profile, setProfile] = useState<BrokerDealerProfileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [isEnriching, setIsEnriching] = useState(false);
  const [attemptedAutoEnrich, setAttemptedAutoEnrich] = useState(false);
  const [isHealthChecking, setIsHealthChecking] = useState(false);
  const [healthCheckResult, setHealthCheckResult] = useState<string | null>(null);
  const [isExtractingFocus, setIsExtractingFocus] = useState(false);
  const [focusResult, setFocusResult] = useState<FocusCeoExtractionResponse | null>(null);
  const [focusError, setFocusError] = useState<string | null>(null);
  const [prevId, setPrevId] = useState<number | null>(null);
  const [nextId, setNextId] = useState<number | null>(null);
  const [isStartingScan, setIsStartingScan] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  // Fetch adjacent IDs for Next/Previous Lead navigation
  useEffect(() => {
    apiRequest<{ prev_id: number | null; next_id: number | null }>(
      `/api/v1/broker-dealers/${brokerDealerId}/adjacent`
    ).then((adj) => {
      setPrevId(adj.prev_id);
      setNextId(adj.next_id);
    }).catch(() => {});
  }, [brokerDealerId]);

  useEffect(() => {
    let active = true;
    async function loadProfile() {
      try {
        const response = await apiRequest<BrokerDealerProfileResponse>(`/api/v1/broker-dealers/${brokerDealerId}/profile`);
        if (active) setProfile(response);
      } catch (loadError) {
        if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load broker-dealer profile.");
      }
    }
    void loadProfile();
    return () => { active = false; };
  }, [brokerDealerId]);

  async function runHealthCheck() {
    setIsHealthChecking(true);
    setHealthCheckResult(null);
    try {
      const result = await apiRequest<{ total_changes: number; fields_refreshed: string[] }>(
        `/api/v1/broker-dealers/${brokerDealerId}/health-check`,
        { method: "POST" }
      );
      if (result.total_changes > 0) {
        setHealthCheckResult(`Updated ${result.total_changes} field(s): ${result.fields_refreshed.join(", ")}`);
        // Reload the profile to reflect changes
        const response = await apiRequest<BrokerDealerProfileResponse>(`/api/v1/broker-dealers/${brokerDealerId}/profile`);
        setProfile(response);
      } else {
        setHealthCheckResult("All data is up to date.");
      }
    } catch (err) {
      setHealthCheckResult(err instanceof Error ? err.message : "Health check failed.");
    } finally {
      setIsHealthChecking(false);
    }
  }

  async function extractFocusCeo() {
    setIsExtractingFocus(true);
    setFocusError(null);
    try {
      const result = await apiRequest<FocusCeoExtractionResponse>(
        `/api/v1/broker-dealers/${brokerDealerId}/extract-focus-ceo`,
        { method: "POST" }
      );
      setFocusResult(result);
      // Reload the full profile to pick up the new ExecutiveContact + any net capital update
      const response = await apiRequest<BrokerDealerProfileResponse>(`/api/v1/broker-dealers/${brokerDealerId}/profile`);
      setProfile(response);
    } catch (err) {
      setFocusError(err instanceof Error ? err.message : "FOCUS extraction failed.");
    } finally {
      setIsExtractingFocus(false);
    }
  }

  async function enrichContacts() {
    setIsEnriching(true);
    setEnrichError(null);
    try {
      const contacts = await apiRequest<BrokerDealerProfileResponse["executive_contacts"]>(
        `/api/v1/broker-dealers/${brokerDealerId}/enrich`,
        { method: "POST" }
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
  }, [attemptedAutoEnrich, isEnriching, profile]);

  if (error) {
    return <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">{error}</div>;
  }

  if (!profile) {
    return (
      <div className="rounded-[30px] border border-white/80 bg-white/92 p-8 shadow-shell">
        <div className="h-6 w-56 animate-pulse rounded bg-slate-100" />
        <div className="mt-4 h-4 w-full animate-pulse rounded bg-slate-100" />
        <div className="mt-8 grid gap-6 xl:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-64 animate-pulse rounded-[28px] bg-slate-100" />
          ))}
        </div>
      </div>
    );
  }

  const { broker_dealer: bd } = profile;
  const chartPoints = profile.financials
    .slice()
    .reverse()
    .map((item) => ({ label: new Date(item.report_date).getFullYear().toString(), value: item.net_capital }));

  return (
    <section className="space-y-6">
      {/* ── Next/Previous Lead Navigation ── */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          disabled={!prevId}
          onClick={() => prevId && router.push(`/master-list/${prevId}`)}
          className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/92 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span>&#8592;</span> Previous Lead
        </button>
        <Link href="/master-list" className="text-sm text-slate-500 hover:text-navy">
          Back to Master List
        </Link>
        <button
          type="button"
          disabled={!nextId}
          onClick={() => nextId && router.push(`/master-list/${nextId}`)}
          className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/92 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Next Lead <span>&#8594;</span>
        </button>
      </div>

      {/* ── Hero Banner ── */}
      <div className="rounded-[30px] bg-navy p-8 text-white shadow-shell">
        <p className="text-sm uppercase tracking-[0.24em] text-white/60">Firm Detail</p>
        <div className="mt-3 flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <h1 className="text-3xl font-semibold">{bd.name}</h1>
            <p className="mt-3 text-sm text-white/70">
              CIK {bd.cik ?? "N/A"} | CRD {bd.crd_number ?? "Pending"} | {bd.city ?? "Unknown"}, {bd.state ?? "Unknown"}
            </p>
            {bd.website ? (
              <a href={bd.website.startsWith("http") ? bd.website : `https://${bd.website}`} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-white/80 underline hover:text-white">
                {bd.website}
              </a>
            ) : null}
          </div>
          <div className="flex flex-col items-end gap-3">
            <div className="flex flex-wrap gap-2">
              <HealthBadge status={bd.health_status} />
              <ClearingTypeBadge type={bd.current_clearing_type} />
              <CompetitorBadge isCompetitor={bd.current_clearing_is_competitor} />
              <ClassificationBadge classification={bd.clearing_classification} />
              <NicheRestrictedBadge isNiche={bd.is_niche_restricted} />
              {bd.lead_priority ? <LeadPriorityBadge priority={bd.lead_priority} score={bd.lead_score} /> : null}
            </div>
            <button
              type="button"
              onClick={() => void runHealthCheck()}
              disabled={isHealthChecking}
              className="rounded-2xl border border-white/30 bg-white/15 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/25 disabled:opacity-60"
            >
              {isHealthChecking ? "Checking..." : "Health Check"}
            </button>
            {healthCheckResult ? (
              <p className="text-xs text-white/70">{healthCheckResult}</p>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── 4-Quadrant Dashboard (Revision 2.1) ── */}
      <div className="grid gap-6 xl:grid-cols-2">

        {/* ── TOP-LEFT: Financials (SEC FOCUS Reports) ── */}
        <QuadrantCard eyebrow="Financials" title="Net capital and trend">
          <div className="mb-5 grid gap-3 md:grid-cols-3">
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Net Capital</p>
              <p className="mt-2 text-lg font-semibold">{formatCurrency(bd.latest_net_capital)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Excess Capital</p>
              <p className="mt-2 text-lg font-semibold">{formatCurrency(bd.latest_excess_net_capital)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">YoY Growth</p>
              {bd.yoy_growth !== null ? (
                <p className={`mt-2 text-lg font-semibold ${bd.yoy_growth >= 0 ? "text-emerald-600" : "text-danger"}`}>
                  {formatPercent(bd.yoy_growth)}
                </p>
              ) : (
                <div className="mt-2">
                  <p className="text-lg font-semibold text-slate-400">N/A</p>
                  <p className="mt-1 text-xs text-slate-400">Requires 2+ years of data</p>
                </div>
              )}
            </div>
          </div>
          <FinancialTrendChart points={chartPoints} />
        </QuadrantCard>

        {/* ── TOP-RIGHT: Assessment (FINRA Detailed Report Overview) ── */}
        <QuadrantCard eyebrow="Assessment" title="Firm profile overview">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Registration Status</p>
              <p className="mt-2">{profile.registration_compliance.registration_status}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Registration Date</p>
              <p className="mt-2">{formatDate(profile.registration_compliance.registration_date)}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Address</p>
              <p className="mt-2">{[bd.city, bd.state].filter(Boolean).join(", ") || "Not available"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
              <p className="font-medium text-navy">Branch Count</p>
              <p className="mt-2">{profile.registration_compliance.branch_count ?? "Not available"}</p>
            </div>
          </div>

          {/* Operations: Types of Business */}
          <div className="mt-4">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-navy">Types of Business</p>
              {bd.types_of_business_total ? (
                <span className="rounded-full bg-blue/10 px-2 py-0.5 text-xs font-medium text-blue">{bd.types_of_business_total} types</span>
              ) : null}
            </div>
            {bd.types_of_business && bd.types_of_business.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {bd.types_of_business.map((type) => (
                  <span key={type} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-700">
                    {type}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-500">Not available</p>
            )}
            {bd.types_of_business_other ? (
              <div className="mt-2 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                <p className="text-xs font-medium text-slate-500">Other Business Activities</p>
                <p className="mt-1">{bd.types_of_business_other}</p>
              </div>
            ) : null}
          </div>

          {bd.website ? (
            <a
              href={bd.website.startsWith("http") ? bd.website : `https://${bd.website}`}
              target="_blank"
              rel="noreferrer"
              className="mt-4 inline-block text-sm font-medium text-blue"
            >
              Visit firm website
            </a>
          ) : null}

          {/* Source PDF downloads + Find emails */}
          <div className="mt-4 flex flex-wrap gap-2">
            <a
              href={`/api/backend/api/v1/broker-dealers/${brokerDealerId}/focus-report.pdf`}
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              <span aria-hidden>↓</span> FOCUS report (PDF)
            </a>
            {bd.crd_number ? (
              <a
                href={`/api/backend/api/v1/broker-dealers/${brokerDealerId}/brokercheck.pdf`}
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <span aria-hidden>↓</span> FINRA BrokerCheck (PDF)
              </a>
            ) : null}
            {(() => {
              // Resolve domain: prefer firm website, fall back to the FOCUS
              // contact's email domain, else disable the button.
              const websiteDomain = bd.website
                ? bd.website.replace(/^https?:\/\//i, "").replace(/\/+$/, "").split("/")[0]?.toLowerCase() ?? null
                : null;
              const contactEmail = profile?.executive_contacts?.find((c) => c.email)?.email ?? null;
              const emailDomain = contactEmail ? contactEmail.split("@")[1]?.toLowerCase() ?? null : null;
              const resolvedDomain = websiteDomain || emailDomain;
              const disabled = !resolvedDomain || isStartingScan;

              const handleClick = async () => {
                if (!resolvedDomain) return;
                setIsStartingScan(true);
                setScanError(null);
                try {
                  const created = await apiRequest<{ id: number }>(
                    "/api/v1/email-extractor/scans",
                    {
                      method: "POST",
                      body: JSON.stringify({
                        domain: resolvedDomain,
                        bd_id: Number(brokerDealerId),
                      }),
                    },
                  );
                  router.push(`/email-extractor/${created.id}` as Route);
                } catch (err) {
                  setScanError(err instanceof Error ? err.message : "Could not start scan");
                  setIsStartingScan(false);
                }
              };

              return (
                <div className="flex flex-col items-start gap-1">
                  <button
                    type="button"
                    onClick={() => void handleClick()}
                    disabled={disabled}
                    title={
                      resolvedDomain
                        ? `Scan ${resolvedDomain} for contact emails`
                        : "No domain on file for this firm"
                    }
                    className="inline-flex items-center gap-1.5 rounded-full bg-navy px-3 py-1.5 text-xs font-medium text-white hover:bg-navy/90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isStartingScan ? "Starting…" : "Find emails"}
                  </button>
                  {scanError ? (
                    <span className="text-xs text-rose-600">{scanError}</span>
                  ) : null}
                </div>
              );
            })()}
          </div>

          {/* FOCUS Report CEO + Net Capital Extraction */}
          <div className="mt-6 border-t border-slate-200 pt-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-navy">FOCUS Report Data</p>
                <p className="mt-1 text-xs text-slate-500">
                  Extracts contact person, phone, email, and net capital from the latest X-17A-5 PDF filing on SEC EDGAR.
                </p>
              </div>
              <button
                type="button"
                onClick={() => void extractFocusCeo()}
                disabled={isExtractingFocus}
                className="shrink-0 rounded-2xl bg-navy px-4 py-2 text-sm font-medium text-white transition hover:bg-[#112b54] disabled:opacity-60 disabled:hover:bg-navy"
              >
                {isExtractingFocus ? (
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Extracting...
                  </span>
                ) : focusResult ? "Re-extract" : "Extract FOCUS Data"}
              </button>
            </div>

            {focusError ? (
              <div className="mt-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-danger">{focusError}</div>
            ) : null}

            {focusResult ? (
              <div className="mt-3 space-y-3">
                {/* Show results or a "no data found" message */}
                {focusResult.ceo_name || focusResult.net_capital !== null ? (
                  <div className="grid gap-2 md:grid-cols-2">
                    {focusResult.ceo_name ? (
                      <div className="rounded-2xl bg-emerald-50 px-4 py-3 text-sm">
                        <p className="font-medium text-navy">Contact Person</p>
                        <p className="mt-1 text-slate-700">{focusResult.ceo_name}</p>
                        {focusResult.ceo_title ? <p className="text-xs text-slate-500">{focusResult.ceo_title}</p> : null}
                      </div>
                    ) : null}
                    {focusResult.ceo_phone ? (
                      <div className="rounded-2xl bg-emerald-50 px-4 py-3 text-sm">
                        <p className="font-medium text-navy">Phone</p>
                        <p className="mt-1 text-slate-700">{focusResult.ceo_phone}</p>
                      </div>
                    ) : null}
                    {focusResult.ceo_email ? (
                      <div className="rounded-2xl bg-emerald-50 px-4 py-3 text-sm">
                        <p className="font-medium text-navy">Email</p>
                        <a href={`mailto:${focusResult.ceo_email}`} className="mt-1 block text-blue">{focusResult.ceo_email}</a>
                      </div>
                    ) : null}
                    {focusResult.net_capital !== null ? (
                      <div className="rounded-2xl bg-emerald-50 px-4 py-3 text-sm">
                        <p className="font-medium text-navy">Net Capital (from PDF)</p>
                        <p className="mt-1 text-lg font-semibold text-slate-700">{formatCurrency(focusResult.net_capital)}</p>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="rounded-2xl bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-700">
                    No contact or net capital data could be extracted from this filing.
                    {focusResult.extraction_status === "no_pdf" ? " This firm has no X-17A-5 PDF on EDGAR." : " The PDF may be scanned or use a non-standard format."}
                  </div>
                )}

                {/* Metadata bar */}
                <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-slate-50 px-4 py-2 text-xs text-slate-500">
                  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${
                    focusResult.extraction_status === "success" ? "bg-emerald-100 text-emerald-700" :
                    focusResult.extraction_status === "error" || focusResult.extraction_status === "no_pdf" ? "bg-red-100 text-red-600" :
                    "bg-amber-100 text-amber-700"
                  }`}>
                    {focusResult.extraction_status === "success" ? "Success" :
                     focusResult.extraction_status === "no_pdf" ? "No PDF" :
                     focusResult.extraction_status === "error" ? "Error" : "Low confidence"}
                  </span>
                  <span>Confidence: {(focusResult.confidence_score * 100).toFixed(0)}%</span>
                  {focusResult.report_date ? <span>Report: {formatDate(focusResult.report_date)}</span> : null}
                  {focusResult.source_pdf_url ? (
                    <a href={focusResult.source_pdf_url} target="_blank" rel="noreferrer" className="text-blue hover:underline">
                      View source PDF
                    </a>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </QuadrantCard>

        {/* ── BOTTOM-LEFT: People (FOCUS Report & CRD Owners) ── */}
        <QuadrantCard eyebrow="People" title="Owners, officers, and contacts">
          {/* Direct Owners from FINRA CRD */}
          {bd.direct_owners && bd.direct_owners.length > 0 ? (
            <div className="mb-4">
              <p className="text-sm font-medium text-navy">Direct Owners</p>
              <div className="mt-2 space-y-2">
                {bd.direct_owners.map((owner, i) => (
                  <div key={`owner-${i}`} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    <p className="font-medium text-navy">{owner.name}</p>
                    {owner.title ? <p className="mt-1">{owner.title}</p> : null}
                    {owner.ownership_pct ? <p className="mt-1 text-xs text-slate-500">Ownership: {owner.ownership_pct}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Executive Officers from FINRA CRD */}
          {bd.executive_officers && bd.executive_officers.length > 0 ? (
            <div className="mb-4">
              <p className="text-sm font-medium text-navy">Executive Officers</p>
              <div className="mt-2 space-y-2">
                {bd.executive_officers.map((officer, i) => (
                  <div key={`officer-${i}`} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    <p className="font-medium text-navy">{officer.name}</p>
                    {officer.title ? <p className="mt-1">{officer.title}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Enriched Executive Contacts (Apollo / ZoomInfo) */}
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-navy">Enriched Contacts</p>
            <button
              type="button"
              onClick={() => void enrichContacts()}
              disabled={isEnriching}
              className="rounded-2xl bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {isEnriching ? "Refreshing..." : "Refresh contacts"}
            </button>
          </div>
          {enrichError ? (
            <div className="mb-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">{enrichError}</div>
          ) : null}
          <div className="space-y-2">
            {profile.executive_contacts.length === 0 && !enrichError ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-6 text-sm text-slate-500">No enriched contacts loaded yet.</div>
            ) : (
              profile.executive_contacts.map((contact) => (
                <div key={`contact-${contact.id}`} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                  <p className="font-medium text-navy">{contact.name}</p>
                  <p className="mt-1">{contact.title}</p>
                  <div className="mt-2 flex flex-wrap gap-3">
                    {contact.email ? <a href={`mailto:${contact.email}`} className="text-blue">{contact.email}</a> : null}
                    {contact.phone ? <span>{contact.phone}</span> : null}
                    {contact.linkedin_url ? <a href={contact.linkedin_url} target="_blank" rel="noreferrer" className="text-blue">LinkedIn</a> : null}
                  </div>
                  <p className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-400">{contact.source} • {formatDate(contact.enriched_at)}</p>
                </div>
              ))
            )}
          </div>
        </QuadrantCard>

        {/* ── BOTTOM-RIGHT: Relationship (Clearing / Introducing) ── */}
        <QuadrantCard eyebrow="Relationship" title="Clearing and introducing mapping">
          {/* Clearing Classification */}
          <div className="mb-4 rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">
            <p className="font-medium text-navy">Clearing Arrangements</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <ClassificationBadge classification={bd.clearing_classification} />
              {!bd.clearing_classification || bd.clearing_classification === "unknown" ? (
                <span className="text-slate-500">Not yet classified</span>
              ) : null}
            </div>
            {bd.clearing_classification === "introducing" && bd.current_clearing_partner ? (
              <p className="mt-2">
                Clearing through: <span className="font-medium text-navy">{bd.current_clearing_partner}</span>
              </p>
            ) : null}
            {/* Logic Override: show raw text if classification failed */}
            {(!bd.clearing_classification || bd.clearing_classification === "unknown") && bd.clearing_raw_text ? (
              <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
                <p className="font-medium">Raw clearing text (classification pending):</p>
                <p className="mt-1 leading-5">{bd.clearing_raw_text}</p>
              </div>
            ) : null}
            {bd.firm_operations_text && bd.clearing_classification && bd.clearing_classification !== "unknown" ? (
              <p className="mt-2 text-xs text-slate-500 leading-5">{bd.firm_operations_text}</p>
            ) : null}
          </div>

          {/* Introducing Arrangements */}
          {profile.introducing_arrangements.length > 0 ? (
            <div className="mb-4">
              <p className="text-sm font-medium text-navy">Introducing Arrangements</p>
              <div className="mt-2 space-y-2">
                {profile.introducing_arrangements.map((arr) => (
                  <div key={arr.id} className="rounded-2xl border border-slate-100 px-4 py-3">
                    {arr.business_name ? (
                      <p className="font-medium text-navy">{arr.business_name}</p>
                    ) : null}
                    {arr.effective_date ? (
                      <p className="mt-1 text-xs text-slate-500">Effective: {formatDate(arr.effective_date)}</p>
                    ) : null}
                    {arr.statement ? (
                      <p className="mt-2 text-sm text-slate-600 leading-6">{arr.statement}</p>
                    ) : null}
                    {arr.description ? (
                      <p className="mt-1 text-sm text-slate-600 leading-6">{arr.description}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* Industry Arrangements — three yes/no statements: books_records, accounts_funds, customer_accounts */}
          {profile.industry_arrangements.length > 0 ? (
            <div className="mb-4">
              <p className="text-sm font-medium text-navy">Industry Arrangements</p>
              <p className="mt-1 text-xs text-slate-500">
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
                  return (
                    <div key={arr.id} className="rounded-2xl border border-slate-100 px-4 py-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-medium text-navy">{kindLabel}</p>
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                            arr.has_arrangement
                              ? "bg-amber-50 text-amber-700"
                              : "bg-emerald-50 text-emerald-700"
                          }`}
                        >
                          {arr.has_arrangement
                            ? "Maintained by a third party"
                            : "Not maintained by a third party"}
                        </span>
                      </div>
                      {arr.has_arrangement ? (
                        <div className="mt-2 space-y-1 text-sm text-slate-600">
                          {arr.partner_name ? (
                            <p>
                              <span className="text-xs uppercase tracking-wide text-slate-400">Partner:</span>{" "}
                              <span className="font-medium text-navy">{arr.partner_name}</span>
                              {arr.partner_crd ? (
                                <span className="ml-2 text-xs text-slate-500">CRD #{arr.partner_crd}</span>
                              ) : null}
                            </p>
                          ) : null}
                          {arr.partner_address ? (
                            <p className="whitespace-pre-line text-xs text-slate-500">{arr.partner_address}</p>
                          ) : null}
                          {arr.effective_date ? (
                            <p className="text-xs text-slate-500">
                              Effective: {formatDate(arr.effective_date)}
                            </p>
                          ) : null}
                          {arr.description ? (
                            <p className="leading-6">{arr.description}</p>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* Deficiency Status */}
          <div className={`mb-4 rounded-2xl px-4 py-4 text-sm ${profile.deficiency_status.is_deficient ? "bg-red-50 text-danger" : "bg-emerald-50 text-emerald-700"}`}>
            <p className="font-medium">
              {profile.deficiency_status.is_deficient ? "Deficiency notice active" : "No active deficiency notice"}
            </p>
            <p className="mt-2 leading-6">{profile.deficiency_status.message}</p>
          </div>

          {/* Clearing Arrangements History */}
          <p className="text-sm font-medium text-navy">Clearing History</p>
          <div className="mt-2 space-y-2">
            {profile.clearing_arrangements.length === 0 ? (
              <div className="rounded-2xl bg-slate-50 px-4 py-6 text-sm text-slate-500">No clearing history available yet.</div>
            ) : (
              profile.clearing_arrangements.map((item) => (
                <div key={item.id} className="rounded-2xl border border-slate-100 px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-navy">{item.clearing_partner ?? "Unknown partner"}</p>
                      <p className="mt-1 text-xs text-slate-500">Year {item.filing_year}</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <ClearingTypeBadge type={item.clearing_type} />
                      <CompetitorBadge isCompetitor={item.is_competitor} />
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </QuadrantCard>
      </div>

      {/* ── Full-width: Filing History ── */}
      <QuadrantCard eyebrow="Filing History" title="Chronological filing timeline">
        <div className="space-y-3">
          {profile.filing_history.length === 0 ? (
            <div className="rounded-2xl bg-slate-50 px-4 py-8 text-sm text-slate-500">No filing history is available yet.</div>
          ) : (
            profile.filing_history.map((item, index) => (
              <div key={`${item.label}-${index}`} className="rounded-2xl border border-slate-100 px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-medium text-navy">{item.label}</p>
                    <p className="mt-1 text-sm text-slate-600">{item.summary}</p>
                  </div>
                  {item.priority ? <AlertPriorityBadge priority={item.priority} /> : null}
                </div>
                <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-500">
                  <span>{formatDate(item.filed_at)}</span>
                  {item.source_filing_url ? (
                    <a href={item.source_filing_url} target="_blank" rel="noreferrer" className="text-blue">
                      Open filing
                    </a>
                  ) : null}
                </div>
              </div>
            ))
          )}
        </div>
      </QuadrantCard>
    </section>
  );
}
