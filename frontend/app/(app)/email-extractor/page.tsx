"use client";

import { AlertCircle, CheckCircle2, Loader2, Mail, Search, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest } from "@/lib/api";

// --- Types (mirror backend/app/schemas/email_extractor.py) -----------------

type RunStatus = "queued" | "running" | "completed" | "failed";

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

const STATUS_STYLES: Record<
  string,
  { className: string; Icon: typeof CheckCircle2; label: string }
> = {
  deliverable: { className: "text-emerald-700 bg-emerald-50", Icon: CheckCircle2, label: "Deliverable" },
  undeliverable: { className: "text-rose-700 bg-rose-50", Icon: XCircle, label: "Undeliverable" },
  inconclusive: { className: "text-amber-700 bg-amber-50", Icon: AlertCircle, label: "Inconclusive" },
  blocked: { className: "text-slate-700 bg-slate-100", Icon: AlertCircle, label: "Blocked" },
};

// --- Helpers ---------------------------------------------------------------

function normalizeDomain(raw: string): string {
  return raw
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/+$/, "")
    .toLowerCase();
}

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
      aria-label={`Email ${status}`}
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
        aria-label="Verify email deliverability"
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

function ResultsTable({
  rows,
  localVerifications,
  inFlightIds,
  verifyErrors,
  onVerify,
}: {
  rows: DiscoveredEmailResponse[];
  localVerifications: Record<number, EmailVerificationResponse>;
  inFlightIds: Set<number>;
  verifyErrors: Record<number, string>;
  onVerify: (emailId: number) => void;
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
                <VerificationCell
                  row={row}
                  localVerification={localVerifications[row.id]}
                  inFlight={inFlightIds.has(row.id)}
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

export default function EmailExtractorPage(): React.ReactElement {
  const [domain, setDomain] = useState("");
  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [localVerifications, setLocalVerifications] = useState<Record<number, EmailVerificationResponse>>({});
  const [inFlightIds, setInFlightIds] = useState<Set<number>>(new Set());
  const [verifyErrors, setVerifyErrors] = useState<Record<number, string>>({});
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

  const isInFlight = scan !== null && !TERMINAL_STATUSES.has(scan.status) && !timedOut;

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
      setInFlightIds((prev) => {
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
      setInFlightIds((prev) => {
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

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const cleaned = normalizeDomain(domain);
    if (!cleaned) return;

    stopPolling();
    stopAllVerifyPolls();
    setError(null);
    setTimedOut(false);
    setScan(null);
    setLocalVerifications({});
    setInFlightIds(new Set());
    setVerifyErrors({});

    try {
      const created = await apiRequest<ScanResponse>("/api/v1/email-extractor/scans", {
        method: "POST",
        body: JSON.stringify({ domain: cleaned }),
      });
      setScan(created);
      startedAtRef.current = Date.now();

      pollRef.current = setInterval(async () => {
        try {
          const next = await apiRequest<ScanResponse>(`/api/v1/email-extractor/scans/${created.id}`);
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
          setError(errorMessage(pollErr, "polling failed"));
        }
      }, POLL_INTERVAL_MS);
    } catch (submitErr) {
      setError(errorMessage(submitErr, "request failed"));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <p className="text-xs uppercase tracking-[0.28em] text-blue">Email Extractor</p>
        <h2 className="mt-1 text-xl font-semibold text-navy">Domain email discovery</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-600">
          Submit a domain and fan out to Hunter, Snov, the in-house site crawler, and theHarvester. Review
          attributed results, then verify deliverability per row via an SMTP RCPT TO probe.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          value={domain}
          onChange={(event) => setDomain(event.target.value)}
          placeholder="alchemydev.io"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          disabled={isInFlight}
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={isInFlight || domain.trim().length === 0}
          className="inline-flex items-center justify-center gap-1.5 rounded-md bg-navy px-4 py-2 text-sm font-medium text-white hover:bg-navy/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Search className="h-4 w-4" />
          Scan domain
        </button>
      </form>

      {scan !== null && (
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
      )}

      {timedOut && (
        <div className="inline-flex items-start gap-2 text-xs text-amber-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>Still running after 3 minutes. Check back later — refresh the page to resume polling.</span>
        </div>
      )}

      {error !== null && (
        <div className="inline-flex items-start gap-2 text-xs text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {scan !== null && (
        <section>
          <ResultsTable
            rows={scan.discovered_emails}
            localVerifications={localVerifications}
            inFlightIds={inFlightIds}
            verifyErrors={verifyErrors}
            onVerify={handleVerify}
          />
        </section>
      )}
    </div>
  );
}
