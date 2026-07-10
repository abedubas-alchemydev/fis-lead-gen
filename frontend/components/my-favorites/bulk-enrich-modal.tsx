"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import { RefreshingIndicator } from "@/components/ui/refreshing-indicator";
import {
  RegenProgress,
  type PhaseSnapshot,
  type PhaseStatus,
} from "@/components/settings/pipelines/regen-progress";
import { useToast } from "@/components/ui/use-toast";
import {
  ApiError,
  bulkEnrichFavoriteList,
  getPipelineRunStatus,
  type PipelineRunDetail,
} from "@/lib/api";

// ── Bulk-enrich a favorite list ────────────────────────────────────────────
// Confirm → run → summary modal for POST /favorite-lists/{id}/enrich. The BE
// runs ONE resilient background job (a parent PipelineRun) that gap-fills every
// firm in the list and always ends with email DISCOVERY; this modal polls
// GET /pipeline/run/{run_id} and renders the live "which firm are we on"
// pointer the BE publishes into the run's `notes` JSON.
//
// Survives a tab close: the job is server-side, so closing this modal (or the
// whole tab) only stops the live view — the run keeps going. Re-opening and
// clicking Start again re-POSTs, and the BE's in-flight guard hands back the
// SAME run_id (status="in_flight"), so polling simply re-attaches instead of
// queueing a duplicate.
//
// Never traps on a dead run: the modal watches the BE's per-firm heartbeat
// (notes.last_progress_at) and the processed_items counter. If neither advances
// for STALE_MS while the run is still non-terminal (its in-process
// BackgroundTask was killed by an instance swap), it declares the run stalled
// and re-enables Start. Crucially this keys off ACTUAL progress, not a fixed
// wall-clock timer, so a slow-but-advancing run — a whole list running past 10
// min, or a firm that just takes a while — is never falsely declared stalled
// and re-dispatched (which would double-bill providers). STALE_MS mirrors the
// BE's STALE_REFRESH_RUN_AGE (10 min) and reads the same heartbeat the BE's
// in-flight guard + reaper judge liveness by, so FE and BE agree on "dead".

const POLL_INTERVAL_MS = 2_000;

// Mirrors backend STALE_REFRESH_RUN_AGE (services/pipeline_reaper.py = 10 min):
// past this with no heartbeat / processed_items movement, the run is presumed
// orphaned — the same signal the BE reaper + in-flight guard use.
const STALE_MS = 10 * 60 * 1_000;

const TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_errors",
  "failed",
]);

type Stage = "confirm" | "starting" | "running" | "done" | "failed";

// Per-item roll-up entry the BE appends to notes.items as each firm finishes.
interface EnrichItemNote {
  id: number;
  type: string;
  name: string;
  status: string; // "done" | "failed" | "skipped"
  reason: string | null;
}

// Shape of the parent run's `notes` JSON while / after running. Kept in sync
// with favorite_list_enrich_orchestrator._progress_notes on the BE.
interface EnrichProgressNotes {
  list_id: number;
  stage: string; // "queued" | "running" | "done"
  total: number;
  current_index: number | null; // 0-based pointer at the firm in flight
  current_name: string | null;
  current_entity_type: string | null;
  // BE heartbeat: re-stamped on every per-firm progress write. The stall
  // detector keys off this (+ processed_items) so liveness matches the BE.
  last_progress_at: string | null;
  items: EnrichItemNote[];
  summary?: string; // present once the run finalizes
}

const ENTITY_LABELS: Record<string, string> = {
  broker_dealer: "Broker-dealer",
  advisor: "Advisor",
  bank: "Bank",
  institutional_investor: "Investor",
  reporting_owner: "Insider",
};

function entityLabel(entityType: string | null | undefined): string {
  if (!entityType) return "";
  return ENTITY_LABELS[entityType] ?? entityType;
}

// Defensive parse — the BE writes JSON, but a poll can catch a transient/empty
// value, so callers must tolerate null.
function parseProgressNotes(notes: string | null): EnrichProgressNotes | null {
  if (!notes) return null;
  try {
    const parsed = JSON.parse(notes) as Partial<EnrichProgressNotes>;
    if (!parsed || typeof parsed !== "object") return null;
    return {
      list_id: typeof parsed.list_id === "number" ? parsed.list_id : 0,
      stage: typeof parsed.stage === "string" ? parsed.stage : "running",
      total: typeof parsed.total === "number" ? parsed.total : 0,
      current_index:
        typeof parsed.current_index === "number" ? parsed.current_index : null,
      current_name:
        typeof parsed.current_name === "string" ? parsed.current_name : null,
      current_entity_type:
        typeof parsed.current_entity_type === "string"
          ? parsed.current_entity_type
          : null,
      last_progress_at:
        typeof parsed.last_progress_at === "string"
          ? parsed.last_progress_at
          : null,
      items: Array.isArray(parsed.items)
        ? (parsed.items as EnrichItemNote[])
        : [],
      summary: typeof parsed.summary === "string" ? parsed.summary : undefined,
    };
  } catch {
    return null;
  }
}

function toPhaseStatus(status: string): PhaseStatus {
  if (status === "done" || status === "failed" || status === "skipped") {
    return status;
  }
  return "running";
}

function itemDetail(item: EnrichItemNote): string | undefined {
  const parts = [entityLabel(item.type), item.reason ?? undefined].filter(
    Boolean,
  ) as string[];
  return parts.length ? parts.join(" · ") : undefined;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

function buildStartErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) {
      return "This list is no longer available. Refresh the page and try again.";
    }
    if (error.status === 401) {
      return "Your session expired. Sign in again to run enrichment.";
    }
    return error.detail || "Couldn't start enrichment.";
  }
  return errorMessage(error);
}

export function BulkEnrichModal({
  listId,
  listName,
  total,
  onClose,
}: {
  listId: number;
  listName: string;
  // Item count shown on the confirm step. The BE re-enumerates authoritatively;
  // this is only for the pre-flight "you're about to enrich N firms" copy.
  total: number;
  onClose: () => void;
}) {
  const toast = useToast();

  const [stage, setStage] = useState<Stage>("confirm");
  const [runId, setRunId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PipelineRunDetail | null>(null);
  const [attached, setAttached] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollWarning, setPollWarning] = useState<string | null>(null);

  const isMountedRef = useRef(true);
  const toastedRef = useRef(false);
  // Stall detector: `key` is a fingerprint of the last progress we saw; `at` is
  // when it last changed. If `at` is older than STALE_MS while non-terminal,
  // the run is presumed dead.
  const progressRef = useRef<{ key: string; at: number }>({ key: "", at: 0 });

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const inFlight = stage === "starting" || stage === "running";

  // Esc closes only when the run isn't live — while in flight the user must use
  // the explicit "close (continues in the background)" link so a stray Esc
  // can't silently drop the live view. Mirrors the Fresh Regen modal.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (inFlight) return;
      onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [inFlight, onClose]);

  async function handleStart() {
    if (stage !== "confirm" && stage !== "failed") return;
    setError(null);
    setPollWarning(null);
    setSummary(null);
    setDetail(null);
    setAttached(false);
    toastedRef.current = false;
    progressRef.current = { key: "", at: Date.now() };
    setStage("starting");

    try {
      const res = await bulkEnrichFavoriteList(listId);
      if (!isMountedRef.current) return;
      if (res.run_id === null) {
        // Empty list (raced between render and click) — no run, no cost.
        const message = res.reason ?? "List is empty — nothing to enrich.";
        setSummary(message);
        toast.info(message);
        setStage("done");
        return;
      }
      setRunId(res.run_id);
      setAttached(res.status === "in_flight");
      setStage("running");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(buildStartErrorMessage(err));
      setStage("failed");
    }
  }

  // ── Poll the parent run until terminal (or stalled) ───────────────────────
  useEffect(() => {
    if (stage !== "running" || runId === null) return;

    let cancelled = false;

    async function tick() {
      try {
        const next = await getPipelineRunStatus(runId as number);
        if (cancelled || !isMountedRef.current) return;
        setDetail(next);
        setPollWarning(null);

        // Advance the stall timer only on GENUINE progress: a firm finished
        // (processed_items climbed) or the BE stamped a fresh per-firm heartbeat
        // (notes.last_progress_at). Keying off these — the same heartbeat the BE
        // judges liveness by — rather than the whole raw notes blob or a
        // wall-clock timer means a long-but-advancing run is never wrongly
        // declared stalled; only a run whose BE heartbeat has truly gone quiet
        // for STALE_MS trips the stall path.
        const heartbeat = parseProgressNotes(next.notes)?.last_progress_at ?? "";
        const key = `${next.status}:${next.processed_items}:${heartbeat}`;
        if (key !== progressRef.current.key) {
          progressRef.current = { key, at: Date.now() };
        }

        const status = next.status.toLowerCase();
        if (TERMINAL_STATUSES.has(status)) {
          finalize(next, status);
          return;
        }

        // Never trap: give up on a run that hasn't moved for STALE_MS.
        if (Date.now() - progressRef.current.at > STALE_MS) {
          if (!toastedRef.current) {
            toastedRef.current = true;
            toast.error(
              "Enrichment stalled — no progress for 10 minutes.",
              { title: "Enrichment stalled" },
            );
          }
          setError(
            "This run hasn't advanced for 10 minutes and looks interrupted. " +
              "Close and click Bulk enrich again to start a fresh run.",
          );
          setStage("failed");
        }
      } catch (err) {
        // Transient poll error — the run may still be alive server-side, so
        // keep ticking (the stall timer still runs). Surface it non-fatally.
        if (!cancelled && isMountedRef.current) {
          setPollWarning(errorMessage(err));
        }
      }
    }

    function finalize(run: PipelineRunDetail, status: string) {
      const parsed = parseProgressNotes(run.notes);
      const skipped = Math.max(
        0,
        run.processed_items - run.success_count - run.failure_count,
      );
      const text =
        parsed?.summary ??
        `${run.success_count} enriched, ${skipped} skipped, ${run.failure_count} failed.`;
      setSummary(text);
      if (!toastedRef.current) {
        toastedRef.current = true;
        if (status === "completed") {
          toast.success(text, { title: "Bulk enrichment complete" });
        } else if (status === "completed_with_errors") {
          toast.info(text, { title: "Bulk enrichment finished with skips" });
        } else {
          toast.error(text || "Bulk enrichment failed.", {
            title: "Bulk enrichment failed",
          });
        }
      }
      setStage(status === "failed" ? "failed" : "done");
    }

    void tick();
    const interval = setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // toast is a stable module-level ref; intentionally excluded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, runId]);

  const notes = useMemo(
    () => parseProgressNotes(detail?.notes ?? null),
    [detail?.notes],
  );

  // Prefer the live BE counters; fall back to notes/props for the very first
  // frames before the first poll resolves.
  const totalItems = detail?.total_items ?? notes?.total ?? total;
  const processed = detail?.processed_items ?? 0;
  const doneCount = detail?.success_count ?? 0;
  const failedCount = detail?.failure_count ?? 0;
  const skippedCount = Math.max(0, processed - doneCount - failedCount);
  const percent =
    totalItems > 0 ? Math.min(100, Math.round((processed / totalItems) * 100)) : 0;

  // Build the per-firm rows newest-first, with the in-flight firm pinned on top
  // so the active work is always visible without scrolling.
  const phases = useMemo<PhaseSnapshot[]>(() => {
    const items = notes?.items ?? [];
    const processedPhases: PhaseSnapshot[] = [...items]
      .reverse()
      .map((item) => ({
        id: `${item.type}-${item.id}`,
        label: item.name,
        status: toPhaseStatus(item.status),
        detail: itemDetail(item),
      }));

    // The BE publishes the pointer BEFORE processing a firm (so items.length ==
    // current_index) and appends the result AFTER (items.length ==
    // current_index + 1). Only synthesize a "running" row in the former case,
    // else the finished firm would double up.
    const showRunning =
      stage === "running" &&
      notes?.current_index != null &&
      notes.current_index >= items.length &&
      !!notes.current_name;

    if (showRunning) {
      return [
        {
          id: "current-running",
          label: notes!.current_name as string,
          status: "running",
          detail: `${entityLabel(notes!.current_entity_type)} · enriching…`,
        },
        ...processedPhases,
      ];
    }
    return processedPhases;
  }, [notes, stage]);

  const pointerLabel = useMemo(() => {
    if (notes?.current_index != null && notes.current_name) {
      return `Enriching ${notes.current_index + 1}/${totalItems}: ${notes.current_name}`;
    }
    if (!detail || detail.status === "queued" || notes?.stage === "queued") {
      return `Preparing to enrich ${totalItems.toLocaleString()} firm${totalItems === 1 ? "" : "s"}…`;
    }
    return "Finalizing…";
  }, [notes, detail, totalItems]);

  const title =
    stage === "confirm"
      ? "Bulk enrich this list"
      : stage === "done"
        ? "Enrichment complete"
        : stage === "failed"
          ? "Enrichment stopped"
          : `Enriching ${listName}`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="bulk-enrich-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (!inFlight) onClose();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative flex max-h-[88vh] w-full max-w-[540px] flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <h2
          id="bulk-enrich-title"
          className="flex items-center gap-2 text-lg font-semibold tracking-tight text-[var(--text,#0f172a)]"
        >
          <Sparkles
            className="h-5 w-5 text-[var(--accent,#6366f1)]"
            strokeWidth={2}
            aria-hidden
          />
          {title}
        </h2>

        {stage === "confirm" ? (
          <ConfirmBody listName={listName} total={total} />
        ) : (
          <div className="mt-4 flex min-h-0 flex-1 flex-col">
            {attached ? (
              <div className="mb-3 rounded-xl border border-[rgba(59,130,246,0.25)] bg-[rgba(59,130,246,0.08)] px-3 py-2 text-xs text-[#1d4ed8]">
                A run for this list was already in progress — resuming its live
                updates here.
              </div>
            ) : null}

            <ProgressHeader
              stage={stage}
              pointerLabel={pointerLabel}
              percent={percent}
              processed={processed}
              total={totalItems}
              doneCount={doneCount}
              skippedCount={skippedCount}
              failedCount={failedCount}
            />

            {phases.length > 0 ? (
              <div className="mt-1 min-h-0 flex-1 overflow-y-auto pr-1">
                <RegenProgress phases={phases} />
              </div>
            ) : stage === "running" ? (
              <p className="mt-4 text-sm text-[var(--text-muted,#94a3b8)]">
                Waiting for the first firm to report…
              </p>
            ) : null}

            {summary && (stage === "done" || stage === "failed") ? (
              <div className="mt-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]/60 px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
                <p className="font-medium text-[var(--text,#0f172a)]">Summary</p>
                <p className="mt-1 text-xs leading-5">{summary}</p>
              </div>
            ) : null}

            {pollWarning && stage === "running" ? (
              <p className="mt-3 text-xs text-[var(--text-muted,#94a3b8)]">
                Live updates degraded ({pollWarning}); the run continues —
                retrying…
              </p>
            ) : null}

            {error ? (
              <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
                {error}
              </div>
            ) : null}
          </div>
        )}

        <ModalActions
          stage={stage}
          inFlight={inFlight}
          onClose={onClose}
          onStart={handleStart}
        />
      </div>
    </div>
  );
}

function ConfirmBody({
  listName,
  total,
}: {
  listName: string;
  total: number;
}) {
  return (
    <div className="mt-3 space-y-4">
      <p className="text-sm leading-6 text-[var(--text-dim,#475569)]">
        This enriches all{" "}
        <span className="font-semibold">{total.toLocaleString()}</span> firm
        {total === 1 ? "" : "s"} in{" "}
        <span className="font-semibold">&lsquo;{listName}&rsquo;</span>. Each
        firm is gap-filled (steps whose data already exists are skipped) and
        always finishes with email discovery.
      </p>
      <div className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-3 py-3 text-xs leading-5 text-amber-700">
        Runs as a background job and calls{" "}
        <span className="font-semibold">paid data providers</span> (FINRA/SEC
        refresh, contact discovery, and email discovery) for each firm. You can
        close this window — it keeps running server-side.
      </div>
    </div>
  );
}

function ProgressHeader({
  stage,
  pointerLabel,
  percent,
  processed,
  total,
  doneCount,
  skippedCount,
  failedCount,
}: {
  stage: Stage;
  pointerLabel: string;
  percent: number;
  processed: number;
  total: number;
  doneCount: number;
  skippedCount: number;
  failedCount: number;
}) {
  return (
    <div className="space-y-3">
      {stage === "running" || stage === "starting" ? (
        <RefreshingIndicator label={pointerLabel} />
      ) : (
        <p className="text-sm font-medium text-[var(--text,#0f172a)]">
          Processed {processed.toLocaleString()} of {total.toLocaleString()}{" "}
          firm{total === 1 ? "" : "s"}.
        </p>
      )}

      <div className="space-y-1">
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-[var(--surface-2,#f1f6fd)]"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div
            className="h-full rounded-full bg-[var(--accent,#6366f1)] transition-[width] duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium">
          <span className="text-[var(--text-muted,#94a3b8)]">
            {processed.toLocaleString()}/{total.toLocaleString()}
          </span>
          <CountChip
            label="done"
            value={doneCount}
            className="border-emerald-200 bg-emerald-50 text-success"
          />
          <CountChip
            label="skipped"
            value={skippedCount}
            className="border-amber-200 bg-amber-50 text-amber-700"
          />
          <CountChip
            label="failed"
            value={failedCount}
            className="border-red-200 bg-red-50 text-danger"
          />
        </div>
      </div>
    </div>
  );
}

function CountChip({
  label,
  value,
  className,
}: {
  label: string;
  value: number;
  className: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${className}`}
    >
      {value.toLocaleString()} {label}
    </span>
  );
}

function ModalActions({
  stage,
  inFlight,
  onClose,
  onStart,
}: {
  stage: Stage;
  inFlight: boolean;
  onClose: () => void;
  onStart: () => void;
}) {
  if (inFlight) {
    return (
      <div className="mt-6 flex flex-col items-end gap-1">
        <button
          type="button"
          onClick={onClose}
          className="text-xs font-medium text-[var(--text-muted,#94a3b8)] underline-offset-4 transition hover:text-[var(--text-dim,#475569)] hover:underline"
        >
          Close (enrichment continues in the background)
        </button>
        <p className="text-[11px] text-[var(--text-muted,#94a3b8)]">
          Closing only stops live updates here. The job keeps running.
        </p>
      </div>
    );
  }

  if (stage === "done") {
    return (
      <div className="mt-6 flex items-center justify-end">
        <Button type="button" onClick={onClose} autoFocus>
          Close
        </Button>
      </div>
    );
  }

  // confirm + failed
  return (
    <div className="mt-6 flex items-center justify-end gap-3">
      <button
        type="button"
        onClick={onClose}
        className={clsx(
          buttonBase,
          buttonSizes.md,
          "border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] font-medium text-[var(--text-dim,#475569)] hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)]",
        )}
      >
        {stage === "failed" ? "Close" : "Cancel"}
      </button>
      <Button type="button" onClick={onStart} autoFocus={stage === "confirm"}>
        {stage === "failed" ? "Retry" : "Start enrichment"}
      </Button>
    </div>
  );
}
