"use client";

import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Linkedin,
  Loader2,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { Route } from "next";
import { useCallback, useEffect, useRef, useState } from "react";

import { EnrichAllButton } from "@/components/email-extractor/enrich-all-button";
import { Pill, type PillVariant } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { useToast } from "@/components/ui/use-toast";
import { apiRequest } from "@/lib/api";
import { formatDate, formatRelativeTime } from "@/lib/format";

// --- Types (mirror backend/app/schemas/email_extractor.py) -----------------

type RunStatus = "queued" | "running" | "completed" | "failed";
type EnrichmentStatus = "not_enriched" | "enriched" | "no_match" | "error";

interface EmailVerificationResponse {
  id: number;
  syntax_valid: boolean | null;
  mx_record_present: boolean | null;
  smtp_status: string;
  smtp_message: string | null;
  checked_at: string;
}

interface DiscoveredEmailResponse {
  id: number;
  email: string;
  domain: string;
  source: string;
  confidence: number | null;
  attribution: string | null;
  bd_id: number | null;
  enriched_name: string | null;
  enriched_title: string | null;
  enriched_linkedin_url: string | null;
  enriched_company: string | null;
  enriched_at: string | null;
  enrichment_status: EnrichmentStatus;
  created_at: string;
  verifications: EmailVerificationResponse[];
}

interface ScanResponse {
  id: number;
  pipeline_name: string;
  domain: string;
  person_name: string | null;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  discovered_emails: DiscoveredEmailResponse[];
}

interface VerifyResultItem {
  email_id: number;
  email: string | null;
  smtp_status: string;
  smtp_message: string | null;
  checked_at: string;
}

interface VerificationRunCreateResponse {
  verify_run_id: number;
  status: RunStatus;
}

interface VerificationRunResponse {
  id: number;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  results: VerifyResultItem[];
}

// --- Constants -------------------------------------------------------------

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 180_000;
const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>(["completed", "failed"]);

// Run-status pill mapping mirrors /email-extractor (hub) so hub and detail
// visually agree on the four states.
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

// SMTP verification → Pill variant + label + icon. Same affordance the rest
// of the design system uses (master-list detail pills).
const SMTP_STATUS_STYLES: Record<
  string,
  { variant: PillVariant; Icon: typeof CheckCircle2; label: string }
> = {
  deliverable: { variant: "healthy", Icon: CheckCircle2, label: "Deliverable" },
  undeliverable: { variant: "critical", Icon: XCircle, label: "Undeliverable" },
  inconclusive: { variant: "warning", Icon: AlertCircle, label: "Inconclusive" },
  blocked: { variant: "unknown", Icon: AlertCircle, label: "Blocked" },
};

const SECONDARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45";

const ROW_BTN =
  "inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50";

// --- Helpers ---------------------------------------------------------------

function formatConfidence(c: number | null): string {
  if (c === null || Number.isNaN(c)) return "—";
  return `×${c.toFixed(2)}`;
}

function latestVerification(
  fromPoll: EmailVerificationResponse[],
  fromLocal: EmailVerificationResponse | undefined,
): EmailVerificationResponse | undefined {
  const candidates: EmailVerificationResponse[] = [...fromPoll];
  if (fromLocal) candidates.push(fromLocal);
  if (candidates.length === 0) return undefined;
  return candidates.reduce((latest, current) =>
    new Date(current.checked_at) > new Date(latest.checked_at) ? current : latest,
  );
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// --- Sub-components --------------------------------------------------------

function StatusPill({ status }: { status: string }): React.ReactElement | null {
  const cfg = SMTP_STATUS_STYLES[status];
  if (!cfg) return null;
  const { variant, Icon, label } = cfg;
  return (
    <Pill variant={variant}>
      <Icon className="h-3 w-3" strokeWidth={2.5} aria-hidden />
      {label}
    </Pill>
  );
}

function VerifyButton({
  emailId,
  inFlight,
  onClick,
  error,
}: {
  emailId: number;
  inFlight: boolean;
  onClick: (emailId: number) => void;
  error: string | undefined;
}): React.ReactElement {
  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => onClick(emailId)}
        disabled={inFlight}
        className={ROW_BTN}
      >
        {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : null}
        {inFlight ? "Verifying…" : "Verify"}
      </button>
      {error ? (
        <span className="text-[11px] text-[var(--pill-red-text,#b91c1c)]">{error}</span>
      ) : null}
    </div>
  );
}

function VerificationCell({
  row,
  localVerification,
  inFlight,
  verifyError,
  onVerify,
}: {
  row: DiscoveredEmailResponse;
  localVerification: EmailVerificationResponse | undefined;
  inFlight: boolean;
  verifyError: string | undefined;
  onVerify: (emailId: number) => void;
}): React.ReactElement {
  const latest = latestVerification(row.verifications, localVerification);

  if (latest && latest.smtp_status !== "not_checked") {
    return <StatusPill status={latest.smtp_status} />;
  }

  const syntaxOk = latest?.syntax_valid === true;
  const mxOk = latest?.mx_record_present === true;
  return (
    <div className="flex flex-col items-start gap-1">
      <span className="inline-flex items-center gap-2 text-[11px] text-[var(--text-dim,#475569)]">
        <span className="inline-flex items-center gap-1">
          {syntaxOk ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--green,#10b981)]" strokeWidth={2} />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" strokeWidth={2} />
          )}
          syntax
        </span>
        <span className="inline-flex items-center gap-1">
          {mxOk ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-[var(--green,#10b981)]" strokeWidth={2} />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" strokeWidth={2} />
          )}
          MX
        </span>
      </span>
      <VerifyButton emailId={row.id} inFlight={inFlight} onClick={onVerify} error={verifyError} />
    </div>
  );
}

function EnrichmentCell({
  row,
  inFlight,
  onEnrich,
}: {
  row: DiscoveredEmailResponse;
  inFlight: boolean;
  onEnrich: (emailId: number) => void;
}): React.ReactElement {
  if (row.enrichment_status === "enriched") {
    return (
      <div className="flex flex-col items-start gap-0.5 text-[12px]">
        {row.enriched_name ? (
          <span className="font-semibold text-[var(--text,#0f172a)]">{row.enriched_name}</span>
        ) : null}
        {row.enriched_title ? (
          <span className="text-[var(--text-dim,#475569)]">{row.enriched_title}</span>
        ) : null}
        {row.enriched_company ? (
          <span className="text-[var(--text-muted,#94a3b8)]">{row.enriched_company}</span>
        ) : null}
        {row.enriched_linkedin_url ? (
          <a
            href={row.enriched_linkedin_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-[#6366f1] hover:underline"
          >
            <Linkedin className="h-3.5 w-3.5" strokeWidth={2} />
            LinkedIn
          </a>
        ) : null}
      </div>
    );
  }

  if (row.enrichment_status === "no_match") {
    return <Pill variant="unknown">Not found</Pill>;
  }

  if (row.enrichment_status === "error") {
    return (
      <div className="flex flex-col items-start gap-1">
        <Pill variant="critical">
          <AlertCircle className="h-3 w-3" strokeWidth={2.5} aria-hidden /> Error
        </Pill>
        <button
          type="button"
          onClick={() => onEnrich(row.id)}
          disabled={inFlight}
          className={ROW_BTN}
        >
          {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : null}
          Retry
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onEnrich(row.id)}
      disabled={inFlight}
      className={ROW_BTN}
    >
      {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : null}
      {inFlight ? "Enriching…" : "Enrich"}
    </button>
  );
}

function ResultsTable({
  rows,
  localVerifications,
  verifyInFlight,
  verifyErrors,
  onVerify,
  enrichInFlight,
  onEnrich,
}: {
  rows: DiscoveredEmailResponse[];
  localVerifications: Record<number, EmailVerificationResponse>;
  verifyInFlight: Set<number>;
  verifyErrors: Record<number, string>;
  onVerify: (emailId: number) => void;
  enrichInFlight: Set<number>;
  onEnrich: (emailId: number) => void;
}): React.ReactElement {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-4 py-10 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
        No emails discovered yet. The scan is either still running or no
        provider returned results for this domain.
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]">
      <table className="w-full text-[13px]">
        <thead className="bg-[var(--surface-2,#f1f6fd)] text-left">
          <tr>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Email
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Source
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Confidence
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Enrichment
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Verification
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
          {rows.map((row) => (
            <tr key={row.id} className="align-top">
              <td className="px-4 py-3 font-mono text-[12px] text-[var(--text,#0f172a)]">
                {row.email}
              </td>
              <td className="px-4 py-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
                {row.source}
              </td>
              <td className="px-4 py-3 text-[12px] tabular-nums text-[var(--text-dim,#475569)]">
                {formatConfidence(row.confidence)}
              </td>
              <td className="px-4 py-3">
                <EnrichmentCell
                  row={row}
                  inFlight={enrichInFlight.has(row.id)}
                  onEnrich={onEnrich}
                />
              </td>
              <td className="px-4 py-3">
                <VerificationCell
                  row={row}
                  localVerification={localVerifications[row.id]}
                  inFlight={verifyInFlight.has(row.id)}
                  verifyError={verifyErrors[row.id]}
                  onVerify={onVerify}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MiniStat({
  label,
  value,
  helper,
}: {
  label: string;
  value: string;
  helper?: string;
}): React.ReactElement {
  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3.5 py-2.5">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p className="mt-1 break-words text-[13px] font-semibold text-[var(--text,#0f172a)]">
        {value}
      </p>
      {helper ? (
        <p className="mt-0.5 text-[11px] text-[var(--text-muted,#94a3b8)]">{helper}</p>
      ) : null}
    </div>
  );
}

// --- Page ------------------------------------------------------------------

export default function ScanDetailPage(): React.ReactElement {
  const params = useParams<{ scanId: string }>();
  const scanId = Number(params?.scanId);

  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [localVerifications, setLocalVerifications] = useState<Record<number, EmailVerificationResponse>>({});
  const [verifyInFlight, setVerifyInFlight] = useState<Set<number>>(new Set());
  const [verifyErrors, setVerifyErrors] = useState<Record<number, string>>({});
  const [enrichInFlight, setEnrichInFlight] = useState<Set<number>>(new Set());
  const toast = useToast();

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef<number>(0);
  const verifyPollsRef = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const stopVerifyPoll = useCallback((emailId: number) => {
    const handle = verifyPollsRef.current.get(emailId);
    if (handle !== undefined) {
      clearInterval(handle);
      verifyPollsRef.current.delete(emailId);
    }
  }, []);

  const stopAllVerifyPolls = useCallback(() => {
    verifyPollsRef.current.forEach((handle) => clearInterval(handle));
    verifyPollsRef.current.clear();
  }, []);

  useEffect(
    () => () => {
      stopPolling();
      stopAllVerifyPolls();
    },
    [stopPolling, stopAllVerifyPolls],
  );

  useEffect(() => {
    if (!scanId || Number.isNaN(scanId)) {
      setLoadError("Invalid scan id");
      return;
    }
    let active = true;

    async function load() {
      try {
        const response = await apiRequest<ScanResponse>(`/api/v1/email-extractor/scans/${scanId}`);
        if (!active) return;
        setScan(response);
        startedAtRef.current = Date.now();
        if (!TERMINAL_STATUSES.has(response.status)) {
          pollRef.current = setInterval(async () => {
            try {
              const next = await apiRequest<ScanResponse>(
                `/api/v1/email-extractor/scans/${scanId}`,
              );
              setScan(next);
              if (TERMINAL_STATUSES.has(next.status)) {
                stopPolling();
                return;
              }
              if (Date.now() - startedAtRef.current > POLL_TIMEOUT_MS) {
                stopPolling();
                setTimedOut(true);
              }
            } catch (pollErr) {
              stopPolling();
              setLoadError(errorMessage(pollErr, "polling failed"));
            }
          }, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (active) setLoadError(errorMessage(err, "Could not load scan"));
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [scanId, stopPolling]);

  const refetchScan = useCallback(async () => {
    if (!scanId || Number.isNaN(scanId)) return;
    try {
      const response = await apiRequest<ScanResponse>(`/api/v1/email-extractor/scans/${scanId}`);
      setScan(response);
    } catch {
      // The EnrichAllButton surfaces its own polling errors; swallow here to
      // avoid stomping on the inline load error banner with transient blips.
    }
  }, [scanId]);

  const isInFlight = scan !== null && !TERMINAL_STATUSES.has(scan.status) && !timedOut;
  const unenrichedCount =
    scan?.discovered_emails.filter((row) => row.enrichment_status !== "enriched").length ?? 0;

  const finishVerify = useCallback(
    (emailId: number, result: VerifyResultItem | undefined, failureMessage: string | null) => {
      stopVerifyPoll(emailId);
      if (result) {
        const verification: EmailVerificationResponse = {
          id: -1,
          syntax_valid: null,
          mx_record_present: null,
          smtp_status: result.smtp_status,
          smtp_message: result.smtp_message,
          checked_at: result.checked_at,
        };
        setLocalVerifications((prev) => ({ ...prev, [emailId]: verification }));
      }
      if (failureMessage !== null) {
        setVerifyErrors((prev) => ({ ...prev, [emailId]: failureMessage }));
      }
      setVerifyInFlight((prev) => {
        const next = new Set(prev);
        next.delete(emailId);
        return next;
      });
    },
    [stopVerifyPoll],
  );

  const handleVerify = useCallback(
    async (emailId: number) => {
      stopVerifyPoll(emailId);
      setVerifyInFlight((prev) => {
        const next = new Set(prev);
        next.add(emailId);
        return next;
      });
      setVerifyErrors((prev) => {
        if (!(emailId in prev)) return prev;
        const next = { ...prev };
        delete next[emailId];
        return next;
      });

      let verifyRunId: number;
      try {
        const created = await apiRequest<VerificationRunCreateResponse>("/api/v1/email-extractor/verify", {
          method: "POST",
          body: JSON.stringify({ email_ids: [emailId] }),
        });
        verifyRunId = created.verify_run_id;
      } catch (err) {
        finishVerify(emailId, undefined, errorMessage(err, "verification failed"));
        return;
      }

      const startedAt = Date.now();
      const handle = setInterval(async () => {
        try {
          const run = await apiRequest<VerificationRunResponse>(
            `/api/v1/email-extractor/verify-runs/${verifyRunId}`,
          );
          if (TERMINAL_STATUSES.has(run.status)) {
            const result = run.results.find((r) => r.email_id === emailId);
            const failure = run.status === "failed" ? run.error_message ?? "verification failed" : null;
            finishVerify(emailId, result, failure);
            return;
          }
          if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
            finishVerify(emailId, undefined, "verification timed out");
          }
        } catch (pollErr) {
          finishVerify(emailId, undefined, errorMessage(pollErr, "verification failed"));
        }
      }, POLL_INTERVAL_MS);
      verifyPollsRef.current.set(emailId, handle);
    },
    [finishVerify, stopVerifyPoll],
  );

  const handleEnrich = useCallback(
    async (emailId: number) => {
      setEnrichInFlight((prev) => {
        const next = new Set(prev);
        next.add(emailId);
        return next;
      });

      try {
        const updated = await apiRequest<DiscoveredEmailResponse>(
          `/api/v1/email-extractor/discovered-emails/${emailId}/enrich`,
          { method: "POST" },
        );
        setScan((prev) =>
          prev === null
            ? prev
            : {
                ...prev,
                discovered_emails: prev.discovered_emails.map((row) =>
                  row.id === emailId ? updated : row,
                ),
              },
        );
      } catch {
        toast.error("Couldn't enrich — please try again.");
      } finally {
        setEnrichInFlight((prev) => {
          const next = new Set(prev);
          next.delete(emailId);
          return next;
        });
      }
    },
    [toast],
  );

  // Loading skeleton: shell + header bars + two metadata pulse cards + table
  // pulse rows. Mirrors the master-list detail loading shell.
  if (scan === null && loadError === null) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="mb-6">
          <div className="h-3 w-72 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-3 h-7 w-72 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-2 h-3 w-96 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={`scan-skeleton-${i}`}
              className="h-40 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]"
            />
          ))}
        </div>
        <div className="mt-4 h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
      </div>
    );
  }

  if (scan === null) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="mb-6">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={"/email-extractor" as Route}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Email Extractor
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Scan
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Scan #{scanId}
          </h1>
        </div>
        <div className="rounded-md border border-red-100 bg-red-50 px-4 py-3 text-[13px] text-[var(--pill-red-text,#b91c1c)]">
          <span className="inline-flex items-start gap-2">
            <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
            <span>{loadError}</span>
          </span>
        </div>
      </div>
    );
  }

  const startedLabel = scan.started_at ? formatRelativeTime(scan.started_at) : "Not started";
  const completedLabel = scan.completed_at ? formatRelativeTime(scan.completed_at) : "—";

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar: breadcrumbs + h1 + meta + right rail ── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={"/email-extractor" as Route}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Email Extractor
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Scan
          </p>
          <h1 className="mt-1 break-all text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            {scan.domain}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <span>
              scan <span className="font-mono text-[var(--text-dim,#475569)]">#{scan.id}</span>
            </span>
            <span aria-hidden>·</span>
            <span>created {formatRelativeTime(scan.created_at)}</span>
            {scan.total_items > 0 ? (
              <>
                <span aria-hidden>·</span>
                <span className="tabular-nums">
                  {scan.processed_items} / {scan.total_items} processed
                </span>
              </>
            ) : null}
            {scan.success_count > 0 || scan.failure_count > 0 ? (
              <>
                <span aria-hidden>·</span>
                <span className="tabular-nums text-[var(--pill-green-text,#047857)]">
                  {scan.success_count} success
                  {scan.success_count === 1 ? "" : "es"}
                </span>
                {scan.failure_count > 0 ? (
                  <span className="tabular-nums text-[var(--pill-red-text,#b91c1c)]">
                    · {scan.failure_count} failure{scan.failure_count === 1 ? "" : "s"}
                  </span>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <Link href={"/email-extractor" as Route} className={SECONDARY_BTN}>
            <ArrowLeft className="h-4 w-4" strokeWidth={2} />
            Back to Email Extractor
          </Link>
        </div>
      </div>

      {/* ── Status pill row ── */}
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT_CLASS[scan.status]}`}
        />
        <Pill variant={STATUS_PILL_VARIANT[scan.status]}>
          {STATUS_PILL_LABEL[scan.status]}
        </Pill>
        {isInFlight ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            polling for updates…
          </span>
        ) : null}
        {scan.error_message ? (
          <span className="text-[12px] text-[var(--pill-red-text,#b91c1c)]">
            {scan.error_message}
          </span>
        ) : null}
      </div>

      {timedOut ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-[12px] text-[var(--pill-amber-text,#b45309)]">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>Still running after 3 minutes. Refresh the page to resume polling.</span>
        </div>
      ) : null}

      {loadError !== null ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>{loadError}</span>
        </div>
      ) : null}

      {/* ── Run metadata ── */}
      <div className="mb-4">
        <SectionPanel eyebrow="Run metadata" title="Scan details">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <MiniStat label="Domain" value={scan.domain} />
            <MiniStat
              label="Person"
              value={scan.person_name ?? "—"}
              helper={scan.person_name ? undefined : "Not specified"}
            />
            <MiniStat label="Pipeline" value={scan.pipeline_name} />
            <MiniStat
              label="Processed"
              value={
                scan.total_items > 0
                  ? `${scan.processed_items} / ${scan.total_items}`
                  : "—"
              }
            />
            <MiniStat
              label="Success / Failure"
              value={`${scan.success_count} / ${scan.failure_count}`}
            />
            <MiniStat
              label="Created"
              value={formatDate(scan.created_at)}
              helper={formatRelativeTime(scan.created_at)}
            />
            <MiniStat
              label="Started"
              value={scan.started_at ? formatDate(scan.started_at) : "—"}
              helper={scan.started_at ? startedLabel : undefined}
            />
            <MiniStat
              label="Completed"
              value={scan.completed_at ? formatDate(scan.completed_at) : "—"}
              helper={scan.completed_at ? completedLabel : undefined}
            />
          </div>
        </SectionPanel>
      </div>

      {/* ── Discovered emails ── */}
      <SectionPanel
        eyebrow="Discovered emails"
        title="Results"
        headerAction={
          <EnrichAllButton
            scanId={scan.id}
            unenrichedCount={unenrichedCount}
            onProgress={() => void refetchScan()}
          />
        }
      >
        <ResultsTable
          rows={scan.discovered_emails}
          localVerifications={localVerifications}
          verifyInFlight={verifyInFlight}
          verifyErrors={verifyErrors}
          onVerify={handleVerify}
          enrichInFlight={enrichInFlight}
          onEnrich={handleEnrich}
        />
      </SectionPanel>
    </div>
  );
}
