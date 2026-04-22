"use client";

import { AlertCircle, Loader2, Search } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { apiRequest } from "@/lib/api";

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

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

const STATUS_PILL: Record<RunStatus, string> = {
  queued: "bg-slate-100 text-slate-600",
  running: "bg-blue/10 text-blue",
  completed: "bg-emerald-50 text-emerald-700",
  failed: "bg-rose-50 text-rose-700",
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

  return (
    <div className="flex flex-col gap-8">
      <header>
        <p className="text-xs uppercase tracking-[0.28em] text-blue">Email Extractor</p>
        <h2 className="mt-1 text-xl font-semibold text-navy">Domain email discovery</h2>
        <p className="mt-2 max-w-2xl text-sm text-slate-600">
          Submit a domain and fan out to Hunter, Snov, the in-house site crawler, and theHarvester.
          Live progress and per-row verification + Apollo enrichment open on the scan page. Past
          scans stay below so you don&apos;t need to re-run them.
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
          disabled={submitting}
          className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-mono text-slate-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={submitting || domain.trim().length === 0}
          className="inline-flex items-center justify-center gap-1.5 rounded-md bg-navy px-4 py-2 text-sm font-medium text-white hover:bg-navy/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          {submitting ? "Starting…" : "Scan domain"}
        </button>
      </form>

      {submitError !== null ? (
        <div className="inline-flex items-start gap-2 text-xs text-rose-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
          <span>{submitError}</span>
        </div>
      ) : null}

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-navy">Recent scans</h3>
          <button
            type="button"
            onClick={() => void loadRecent()}
            disabled={historyLoading}
            className="text-xs text-blue hover:underline disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        {historyError ? (
          <div className="inline-flex items-start gap-2 text-xs text-rose-700">
            <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <span>{historyError}</span>
          </div>
        ) : null}

        {recentScans.length === 0 && !historyLoading && !historyError ? (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-8 text-center text-sm text-slate-500">
            No scans yet. Submit a domain above to start.
          </div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Domain</th>
                  <th className="px-4 py-2 font-medium">Started</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Emails</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {recentScans.map((scan) => (
                  <tr key={scan.id} className="hover:bg-slate-50">
                    <td className="px-4 py-2">
                      <Link
                        href={`/email-extractor/${scan.id}` as Route}
                        className="font-mono text-xs text-navy hover:underline"
                      >
                        {scan.domain}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-600">
                      {formatRelative(scan.created_at)}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${STATUS_PILL[scan.status]}`}
                      >
                        {scan.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs tabular-nums text-slate-700">
                      {scan.success_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
