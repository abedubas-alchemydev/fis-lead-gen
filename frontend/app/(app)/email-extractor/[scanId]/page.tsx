"use client";

import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Linkedin,
  Loader2,
  Mail,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { Route } from "next";
import { useCallback, useEffect, useRef, useState } from "react";

import { EnrichAllButton } from "@/components/email-extractor/enrich-all-button";
import { apiRequest } from "@/lib/api";

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

const STATUS_STYLES: Record<string, { className: string; Icon: typeof CheckCircle2; label: string }> = {
  deliverable: { className: "text-emerald-700 bg-emerald-50", Icon: CheckCircle2, label: "Deliverable" },
  undeliverable: { className: "text-rose-700 bg-rose-50", Icon: XCircle, label: "Undeliverable" },
  inconclusive: { className: "text-amber-700 bg-amber-50", Icon: AlertCircle, label: "Inconclusive" },
  blocked: { className: "text-slate-700 bg-slate-100", Icon: AlertCircle, label: "Blocked" },
};

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
  const cfg = STATUS_STYLES[status];
  if (!cfg) return null;
  const { className, Icon, label } = cfg;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium ${className}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </span>
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
        className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        {inFlight ? "Verifying…" : "Verify"}
      </button>
      {error ? <span className="text-xs text-rose-600">{error}</span> : null}
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
      <span className="inline-flex items-center gap-2 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1">
          {syntaxOk ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-slate-400" />
          )}
          syntax
        </span>
        <span className="inline-flex items-center gap-1">
          {mxOk ? (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
          ) : (
            <XCircle className="h-3.5 w-3.5 text-slate-400" />
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
  enrichError,
  onEnrich,
}: {
  row: DiscoveredEmailResponse;
  inFlight: boolean;
  enrichError: string | undefined;
  onEnrich: (emailId: number) => void;
}): React.ReactElement {
  if (row.enrichment_status === "enriched") {
    return (
      <div className="flex flex-col items-start gap-0.5 text-xs">
        {row.enriched_name ? (
          <span className="font-medium text-navy">{row.enriched_name}</span>
        ) : null}
        {row.enriched_title ? (
          <span className="text-slate-600">{row.enriched_title}</span>
        ) : null}
        {row.enriched_company ? (
          <span className="text-slate-500">{row.enriched_company}</span>
        ) : null}
        {row.enriched_linkedin_url ? (
          <a
            href={row.enriched_linkedin_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-blue hover:underline"
          >
            <Linkedin className="h-3.5 w-3.5" />
            LinkedIn
          </a>
        ) : null}
      </div>
    );
  }

  if (row.enrichment_status === "no_match") {
    return (
      <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
        Not found
      </span>
    );
  }

  if (row.enrichment_status === "error") {
    return (
      <div className="flex flex-col items-start gap-1">
        <span
          className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700"
          title={enrichError ?? "Enrichment failed — click to retry"}
        >
          <AlertCircle className="h-3.5 w-3.5" /> Error
        </span>
        <button
          type="button"
          onClick={() => onEnrich(row.id)}
          disabled={inFlight}
          className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => onEnrich(row.id)}
        disabled={inFlight}
        className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {inFlight ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        {inFlight ? "Enriching…" : "Enrich"}
      </button>
      {enrichError ? <span className="text-xs text-rose-600">{enrichError}</span> : null}
    </div>
  );
}

function ResultsTable({
  rows,
  localVerifications,
  verifyInFlight,
  verifyErrors,
  onVerify,
  enrichInFlight,
  enrichErrors,
  onEnrich,
}: {
  rows: DiscoveredEmailResponse[];
  localVerifications: Record<number, EmailVerificationResponse>;
  verifyInFlight: Set<number>;
  verifyErrors: Record<number, string>;
  onVerify: (emailId: number) => void;
  enrichInFlight: Set<number>;
  enrichErrors: Record<number, string>;
  onEnrich: (emailId: number) => void;
}): React.ReactElement {
  if (rows.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center text-sm text-slate-500">
        <Mail className="mx-auto mb-2 h-5 w-5 opacity-50" />
        No emails found yet.
        <div className="mt-1 text-xs opacity-75">
          The scan is either still running or no provider returned results for this domain.
        </div>
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2 font-medium">Email</th>
            <th className="px-4 py-2 font-medium">Source</th>
            <th className="px-4 py-2 font-medium">Confidence</th>
            <th className="px-4 py-2 font-medium">Enrichment</th>
            <th className="px-4 py-2 font-medium">Verification</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="px-4 py-2 font-mono text-xs text-slate-800">{row.email}</td>
              <td className="px-4 py-2 text-xs text-slate-600">{row.source}</td>
              <td className="px-4 py-2 text-xs tabular-nums text-slate-700">{formatConfidence(row.confidence)}</td>
              <td className="px-4 py-2">
                <EnrichmentCell
                  row={row}
                  inFlight={enrichInFlight.has(row.id)}
                  enrichError={enrichErrors[row.id]}
                  onEnrich={onEnrich}
                />
              </td>
              <td className="px-4 py-2">
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
  const [enrichErrors, setEnrichErrors] = useState<Record<number, string>>({});

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
      setEnrichErrors((prev) => {
        if (!(emailId in prev)) return prev;
        const next = { ...prev };
        delete next[emailId];
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
      } catch (err) {
        setEnrichErrors((prev) => ({
          ...prev,
          [emailId]: errorMessage(err, "enrichment failed"),
        }));
      } finally {
        setEnrichInFlight((prev) => {
          const next = new Set(prev);
          next.delete(emailId);
          return next;
        });
      }
    },
    [],
  );

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href={"/email-extractor" as Route}
          className="inline-flex items-center gap-1 text-xs font-medium text-blue hover:underline"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Email Extractor
        </Link>
      </div>

      <header>
        <p className="text-xs uppercase tracking-[0.28em] text-blue">Email Extractor</p>
        <h2 className="mt-1 text-xl font-semibold text-navy">
          Scan {scan ? `— ${scan.domain}` : `#${scanId}`}
        </h2>
        {scan ? (
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Submitted {new Date(scan.created_at).toLocaleString()}. Results stay on this page so you
            can return later without re-running.
          </p>
        ) : null}
      </header>

      {scan !== null ? (
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
          {isInFlight && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          <span>
            Status: <span className="font-medium text-navy">{scan.status}</span>
          </span>
          {scan.total_items > 0 && (
            <span className="tabular-nums">
              {scan.processed_items} / {scan.total_items}
            </span>
          )}
          <span className="ml-auto font-mono text-slate-500">scan #{scan.id}</span>
        </div>
      ) : null}

      {timedOut ? (
        <div className="inline-flex items-start gap-2 text-xs text-amber-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>Still running after 3 minutes. Refresh the page to resume polling.</span>
        </div>
      ) : null}

      {loadError !== null ? (
        <div className="inline-flex items-start gap-2 text-xs text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>{loadError}</span>
        </div>
      ) : null}

      {scan !== null ? (
        <section>
          <div className="mb-3 flex items-start justify-end">
            <EnrichAllButton
              scanId={scan.id}
              unenrichedCount={unenrichedCount}
              onProgress={() => void refetchScan()}
            />
          </div>
          <ResultsTable
            rows={scan.discovered_emails}
            localVerifications={localVerifications}
            verifyInFlight={verifyInFlight}
            verifyErrors={verifyErrors}
            onVerify={handleVerify}
            enrichInFlight={enrichInFlight}
            enrichErrors={enrichErrors}
            onEnrich={handleEnrich}
          />
        </section>
      ) : null}
    </div>
  );
}
