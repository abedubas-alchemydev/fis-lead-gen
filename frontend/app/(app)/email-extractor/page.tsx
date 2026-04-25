"use client";

import { AlertCircle, ArrowRight, Loader2, RefreshCw, Search } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { TopActions } from "@/components/layout/top-actions";
import { Pill, type PillVariant } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { apiRequest } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";

// --- Types (mirror backend/app/schemas/email_extractor.py) -----------------

type RunStatus = "queued" | "running" | "completed" | "failed";

interface ScanListItem {
  id: number;
  domain: string;
  person_name: string | null;
  bd_id: number | null;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

interface ScanCreateResponse {
  id: number;
}

// --- Helpers ---------------------------------------------------------------

function normalizeDomain(raw: string): string {
  return raw
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/+$/, "")
    .toLowerCase();
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// Status → Pill variant + label + dot color. Variants come from
// components/ui/pill.tsx so dark-mode tokens flow through automatically;
// dot halos mirror the alerts-client priority-dot treatment.
const STATUS_PILL_VARIANT: Record<RunStatus, PillVariant> = {
  queued: "unknown",
  running: "info",
  completed: "healthy",
  failed: "critical",
};

const STATUS_PILL_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

const STATUS_DOT_CLASS: Record<RunStatus, string> = {
  queued:
    "bg-[var(--text-muted,#94a3b8)] shadow-[0_0_0_4px_rgba(148,163,184,0.15)]",
  running:
    "bg-[var(--blue,#3b82f6)] shadow-[0_0_0_4px_rgba(59,130,246,0.15)] animate-pulse",
  completed:
    "bg-[var(--green,#10b981)] shadow-[0_0_0_4px_rgba(16,185,129,0.15)]",
  failed: "bg-[var(--red,#ef4444)] shadow-[0_0_0_4px_rgba(239,68,68,0.15)]",
};

// --- Page ------------------------------------------------------------------

export default function EmailExtractorHomePage(): React.ReactElement {
  const router = useRouter();
  const [domain, setDomain] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [recentScans, setRecentScans] = useState<ScanListItem[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);

  const loadRecent = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const response = await apiRequest<ScanListItem[]>(
        "/api/v1/email-extractor/scans?limit=20",
      );
      setRecentScans(response);
      setHistoryError(null);
    } catch (err) {
      setHistoryError(errorMessage(err, "Could not load recent scans"));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRecent();
  }, [loadRecent]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const cleaned = normalizeDomain(domain);
    if (!cleaned) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await apiRequest<ScanCreateResponse>("/api/v1/email-extractor/scans", {
        method: "POST",
        body: JSON.stringify({ domain: cleaned }),
      });
      router.push(`/email-extractor/${created.id}` as Route);
    } catch (err) {
      setSubmitError(errorMessage(err, "Could not start scan"));
      setSubmitting(false);
    }
  }

  // Live-match counts: total scans loaded + sum of discovered emails.
  const totalEmails = useMemo(
    () => recentScans.reduce((sum, scan) => sum + scan.success_count, 0),
    [recentScans],
  );

  const submitDisabled = submitting || domain.trim().length === 0;

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ───────────────────────────────────────────────────────── */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Email Extractor
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Domain email discovery
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      {/* ── Live-match pill row (mirrors master-list / alerts) ───────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {recentScans.length.toLocaleString()} scan
          {recentScans.length === 1 ? "" : "s"} loaded
        </span>
        {totalEmails > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[#4338ca]">
            {totalEmails.toLocaleString()} email{totalEmails === 1 ? "" : "s"} discovered
          </span>
        ) : null}
      </div>

      {/* ── New-scan SectionPanel ───────────────────────────────────────── */}
      <div className="mb-4">
        <SectionPanel
          eyebrow="New scan"
          title="Start a domain discovery"
        >
          <p className="mb-4 max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Submit a domain and fan out to Hunter, Snov, the in-house site crawler,
            and theHarvester. Live progress and per-row verification + Apollo
            enrichment open on the scan page. Past scans stay below so you don&apos;t
            need to re-run them.
          </p>
          <form
            onSubmit={handleSubmit}
            className="flex flex-col gap-3 sm:flex-row sm:items-end"
          >
            <div className="flex-1">
              <label
                htmlFor="email-extractor-domain"
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
              >
                Domain
              </label>
              <input
                id="email-extractor-domain"
                type="text"
                value={domain}
                onChange={(event) => setDomain(event.target.value)}
                placeholder="alchemydev.io"
                spellCheck={false}
                autoCapitalize="off"
                autoCorrect="off"
                disabled={submitting}
                className="h-[38px] w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 font-mono text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] disabled:opacity-60"
              />
            </div>
            <button
              type="submit"
              disabled={submitDisabled}
              className="inline-flex h-[38px] items-center justify-center gap-1.5 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:shadow-[0_8px_22px_rgba(99,102,241,0.45)] disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
              ) : (
                <Search className="h-4 w-4" strokeWidth={2} />
              )}
              {submitting ? "Starting…" : "Scan domain"}
            </button>
          </form>

          {submitError !== null ? (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
              <span>{submitError}</span>
            </div>
          ) : null}
        </SectionPanel>
      </div>

      {historyError ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {historyError}
        </div>
      ) : null}

      {/* ── Recent scans SectionPanel ───────────────────────────────────── */}
      <SectionPanel
        eyebrow="History"
        title="Recent scans"
        headerAction={
          <button
            type="button"
            onClick={() => void loadRecent()}
            disabled={historyLoading}
            className="inline-flex items-center gap-1.5 rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${historyLoading ? "animate-spin" : ""}`}
              strokeWidth={2}
            />
            {historyLoading ? "Refreshing…" : "Refresh"}
          </button>
        }
      >
        {historyLoading && recentScans.length === 0 ? (
          <div>
            {Array.from({ length: 6 }).map((_, index) => (
              <div
                key={`scan-loading-${index}`}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <div className="h-3 w-32 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-4 w-48 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-3 w-64 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
            ))}
          </div>
        ) : recentScans.length === 0 ? (
          <div className="my-2 rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-4 py-10 text-center text-sm text-[var(--text-muted,#94a3b8)]">
            No scans yet. Submit a domain above to start.
          </div>
        ) : (
          <div>
            {recentScans.map((scan) => (
              <div
                key={scan.id}
                className="flex gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <span
                  aria-hidden
                  className={`mt-2 h-2 w-2 shrink-0 rounded-full ${STATUS_DOT_CLASS[scan.status]}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <Pill variant={STATUS_PILL_VARIANT[scan.status]}>
                      {STATUS_PILL_LABEL[scan.status]}
                    </Pill>
                    <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
                      {formatRelativeTime(scan.created_at)}
                    </span>
                  </div>
                  <Link
                    href={`/email-extractor/${scan.id}` as Route}
                    className="mb-1 block font-mono text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
                  >
                    {scan.domain}
                  </Link>
                  <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                    <span className="tabular-nums">
                      {scan.success_count.toLocaleString()}
                    </span>{" "}
                    email{scan.success_count === 1 ? "" : "s"} discovered
                    {scan.failure_count > 0 ? (
                      <>
                        {" "}
                        <span className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
                        <span className="text-[var(--pill-red-text,#b91c1c)]">
                          {scan.failure_count.toLocaleString()} failure
                          {scan.failure_count === 1 ? "" : "s"}
                        </span>
                      </>
                    ) : null}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Link
                      href={`/email-extractor/${scan.id}` as Route}
                      className="inline-flex items-center gap-1 rounded-md border border-[rgba(99,102,241,0.3)] px-2.5 py-1 text-[11px] font-semibold text-[#6366f1] transition hover:bg-[rgba(99,102,241,0.05)]"
                    >
                      View scan
                      <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
                    </Link>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionPanel>
    </div>
  );
}
