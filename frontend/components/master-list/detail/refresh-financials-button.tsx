"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import { useToast } from "@/components/ui/use-toast";
import {
  ApiError,
  getPipelineRunStatus,
  refreshFinancials,
  type PipelineRunDetail,
  type PipelineRunStatus,
} from "@/lib/api";

// User-clickable button rendered next to an UnknownCell tooltip when the
// reason category is `not_yet_extracted` for a financial-pipeline field.
//
// Click flow:
//   1. POST /broker-dealers/{id}/refresh-financials
//        - 202: capture run_id, start polling
//        - 409: api.ts normalizes the existing run_id into the success
//          shape, so we just poll
//        - 503: provider key missing on server — generic toast, no retry
//        - 401: session expired — surface a toast; auth middleware will
//          bounce on the next nav
//        - 404 / other: silent-fail-on-error (mirrors firm-website-link)
//   2. Poll /pipeline/run/{run_id} every 3s until terminal status. Soft
//      cap at 120s — afterward drop to 10s cadence and keep polling so
//      the UI eventually catches up if the BE finishes late.
//   3. On `completed` / `completed_with_errors`: router.refresh() to
//      pull the now-populated bd record. The button vanishes because
//      the surrounding UnknownCell unmounts once unknown_reason clears.
//   4. On `failed`: parse notes JSON, surface the error string verbatim.
//
// StrictMode-safe via firedRef (matches firm-website-link pattern); a
// cancelledRef tracks unmount so timers can't setState on a dead tree.

const POLL_INTERVAL_MS = 3_000;
const SLOW_POLL_INTERVAL_MS = 10_000;
const SOFT_TIMEOUT_MS = 120_000;

const TERMINAL_STATUSES: ReadonlySet<PipelineRunStatus> = new Set([
  "completed",
  "completed_with_errors",
  "failed",
]);

interface NotesPayload {
  summary?: string;
  error?: string;
  stage?: string;
  bd_id?: number;
}

function parseNotes(notes: string | null): NotesPayload {
  if (!notes) return {};
  try {
    const parsed = JSON.parse(notes) as unknown;
    if (parsed && typeof parsed === "object") {
      return parsed as NotesPayload;
    }
  } catch {
    // BE has shipped non-JSON notes before; fall through to empty.
  }
  return {};
}

function nextDelayMs(elapsedMs: number): number {
  return elapsedMs >= SOFT_TIMEOUT_MS ? SLOW_POLL_INTERVAL_MS : POLL_INTERVAL_MS;
}

interface RefreshFinancialsButtonProps {
  firmId: number;
  // Smaller variant for inline placement next to a tooltip icon inside
  // a stat tile or table cell.
  compact?: boolean;
}

export function RefreshFinancialsButton({
  firmId,
  compact = false,
}: RefreshFinancialsButtonProps) {
  const router = useRouter();
  const toast = useToast();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const firedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const slowToastFiredRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  function handleTerminal(run: PipelineRunDetail) {
    const notes = parseNotes(run.notes);
    if (run.status === "failed") {
      const errMsg = notes.error ?? "Refresh failed.";
      toast.error(`Refresh failed: ${errMsg}`);
      setIsRefreshing(false);
      firedRef.current = false;
      return;
    }
    if (run.status === "completed_with_errors" && notes.error) {
      toast.info(`Refresh finished with warnings: ${notes.error}`);
    }
    router.refresh();
    setIsRefreshing(false);
    firedRef.current = false;
  }

  function scheduleNextPoll(runId: number, startedAt: number) {
    if (cancelledRef.current) return;
    const elapsed = Date.now() - startedAt;
    if (elapsed >= SOFT_TIMEOUT_MS && !slowToastFiredRef.current) {
      slowToastFiredRef.current = true;
      toast.info("Refresh is taking longer than expected. Still working…");
    }
    timerRef.current = setTimeout(() => {
      void pollOnce(runId, startedAt);
    }, nextDelayMs(elapsed));
  }

  async function pollOnce(runId: number, startedAt: number) {
    if (cancelledRef.current) return;
    try {
      const run = await getPipelineRunStatus(runId);
      if (cancelledRef.current) return;
      if (TERMINAL_STATUSES.has(run.status)) {
        handleTerminal(run);
        return;
      }
      scheduleNextPoll(runId, startedAt);
    } catch (err) {
      if (cancelledRef.current) return;
      // 404 means the run vanished — surface and stop. Other errors
      // are treated as transient; keep polling under the soft cap.
      if (err instanceof ApiError && err.status === 404) {
        toast.error("Refresh run not found on server.");
        setIsRefreshing(false);
        firedRef.current = false;
        return;
      }
      scheduleNextPoll(runId, startedAt);
    }
  }

  async function handleClick() {
    if (firedRef.current || isRefreshing) return;
    firedRef.current = true;
    setIsRefreshing(true);
    slowToastFiredRef.current = false;

    try {
      const { run_id } = await refreshFinancials(firmId);
      if (cancelledRef.current) return;
      const startedAt = Date.now();
      timerRef.current = setTimeout(() => {
        void pollOnce(run_id, startedAt);
      }, POLL_INTERVAL_MS);
    } catch (err) {
      if (cancelledRef.current) return;
      if (err instanceof ApiError) {
        if (err.status === 503) {
          toast.error("Pipeline temporarily unavailable. Try again later.");
        } else if (err.status === 401) {
          toast.error("Your session has expired. Please sign in again.");
        } else if (err.status === 404) {
          toast.error("Firm not found.");
        } else {
          toast.error("Could not start refresh. Try again later.");
        }
      }
      setIsRefreshing(false);
      firedRef.current = false;
    }
  }

  const baseClass = compact
    ? "inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50"
    : "inline-flex items-center gap-1.5 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2 py-1 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50";

  const iconClass = compact ? "h-3 w-3" : "h-3.5 w-3.5";

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={isRefreshing}
      title={
        isRefreshing
          ? "Running financial extraction (~30-90s)"
          : "Run the financial extraction pipeline for this firm"
      }
      aria-label="Refresh financials"
      data-testid="refresh-financials-button"
      className={baseClass}
    >
      {isRefreshing ? (
        <>
          <Loader2 className={`${iconClass} animate-spin`} strokeWidth={2} aria-hidden />
          <span>Running…</span>
        </>
      ) : (
        <>
          <RefreshCw className={iconClass} strokeWidth={2} aria-hidden />
          <span>Refresh financials</span>
        </>
      )}
    </button>
  );
}
