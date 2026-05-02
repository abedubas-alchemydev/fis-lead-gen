"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import { useToast } from "@/components/ui/use-toast";
import {
  ApiError,
  getPipelineRunStatus,
  refreshFirm,
  type PipelineRunDetail,
  type PipelineRunStatus,
} from "@/lib/api";

// User-clickable button that triggers the BE's selective per-firm
// refresh-all orchestrator. Rendered exactly once per row (master-list
// table leftmost column) or per page (firm-detail h1 region) when
// isFirmIncomplete(firm) === true.
//
// Click flow:
//   1. POST /broker-dealers/{id}/refresh-all
//        - 200 + status="skipped" + run_id=null → BE found nothing to do.
//          Show a confirmation toast and router.refresh(). Zero credit.
//        - 202 + status="queued" + run_id=N → at least one sub-pipeline
//          fired. Poll /pipeline/run/N until terminal.
//        - 409 → api.ts normalizes to the same { run_id, status } shape.
//          Caller polls the existing in-flight run.
//        - 429 cooldown → "Slow down" toast.
//        - 503 → "Pipeline temporarily unavailable" toast (BE message
//          names the missing provider for ops).
//        - 401 → session-expired toast.
//        - 404 → "Firm not found" toast.
//   2. Poll every 3s until terminal. Soft cap at 120s — drop to 10s
//      cadence afterward and keep polling so the UI eventually catches
//      up if the BE finishes late.
//   3. On `completed` / `completed_with_errors`: parse parent run's
//      notes.summary ("Refreshed: financials, website. Skipped:
//      clearing, contacts.") and show it verbatim, then router.refresh()
//      to pull the now-populated row.
//   4. On `failed`: parse notes for an error string and surface verbatim.
//
// StrictMode-safe via firedRef; unmount-safe via cancelledRef.

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
  ran?: string[];
  skipped?: string[];
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

interface RefreshFirmButtonProps {
  firmId: number;
  // Smaller variant for inline placement inside a table row's leftmost
  // cell. Defaults to the larger detail-page variant.
  compact?: boolean;
  // When true, programmatically triggers handleClick once on mount —
  // equivalent to the user clicking immediately on visit. Used by the
  // firm-detail page so visiting an incomplete firm auto-fills missing
  // data without requiring a manual click. Master-list grid rows leave
  // this false (default) so a list view doesn't fire 25 simultaneous
  // refresh-alls. The BE's 30-second per-(user, BD) cooldown already
  // protects against rapid revisits to the same firm.
  autoFire?: boolean;
}

export function RefreshFirmButton({
  firmId,
  compact = false,
  autoFire = false,
}: RefreshFirmButtonProps) {
  const router = useRouter();
  const toast = useToast();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const firedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelledRef = useRef(false);
  const slowToastFiredRef = useRef(false);
  const autoFireTriggeredRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  // Auto-fire on mount when requested. autoFireTriggeredRef guards against
  // StrictMode's dev double-invoke. firedRef inside handleClick is the
  // post-fire guard for the click path.
  useEffect(() => {
    if (!autoFire || autoFireTriggeredRef.current) return;
    autoFireTriggeredRef.current = true;
    void handleClick();
    // handleClick is stable within this component instance; lint exhaustive-deps
    // would otherwise force us to memoize it (no win, just churn).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFire]);

  function handleTerminal(run: PipelineRunDetail) {
    const notes = parseNotes(run.notes);
    if (run.status === "failed") {
      const errMsg = notes.error ?? "Refresh failed.";
      toast.error(`Refresh failed: ${errMsg}`);
      setIsRefreshing(false);
      firedRef.current = false;
      return;
    }
    // completed or completed_with_errors. Surface notes.summary verbatim
    // so the user knows which sub-pipelines actually ran vs. were
    // skipped. The BE caps summary at ~180 chars per the prompt.
    const summary = notes.summary ?? "Refreshed.";
    if (run.status === "completed_with_errors") {
      toast.info(`Finished with warnings — ${summary}`);
    } else {
      toast.success(summary);
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
      // 404 means the run vanished — surface and stop. Other errors are
      // transient; keep polling under the soft cap.
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
      const result = await refreshFirm(firmId);
      if (cancelledRef.current) return;

      if (result.status === "skipped" || result.run_id === null) {
        // BE found nothing to do — already complete. No polling needed.
        toast.success(result.reason ?? "Already complete.");
        router.refresh();
        setIsRefreshing(false);
        firedRef.current = false;
        return;
      }

      const startedAt = Date.now();
      const runId = result.run_id;
      timerRef.current = setTimeout(() => {
        void pollOnce(runId, startedAt);
      }, POLL_INTERVAL_MS);
    } catch (err) {
      if (cancelledRef.current) return;
      if (err instanceof ApiError) {
        if (err.status === 503) {
          toast.error("Pipeline temporarily unavailable. Try again later.");
        } else if (err.status === 429) {
          toast.error("Slow down — try again in a few seconds.");
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
    ? "inline-flex items-center justify-center rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] p-1.5 text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50"
    : "inline-flex items-center gap-1.5 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50";

  const iconClass = "h-3.5 w-3.5";

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={isRefreshing}
      title={
        isRefreshing
          ? "Refreshing missing fields (~30-90s)"
          : "Fetch missing data for this firm"
      }
      aria-label="Refresh firm data"
      data-testid="refresh-firm-button"
      className={baseClass}
    >
      {isRefreshing ? (
        <Loader2
          className={`${iconClass} animate-spin`}
          strokeWidth={2}
          aria-hidden
        />
      ) : (
        <RefreshCw className={iconClass} strokeWidth={2} aria-hidden />
      )}
      {compact ? null : (
        <span>{isRefreshing ? "Refreshing…" : "Refresh firm"}</span>
      )}
    </button>
  );
}
