"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  findPipelineRun,
  runInitialLoad,
  runPopulateAll,
  setFilesApiFlag,
  wipeBdData,
  type SetFilesApiFlagResponse
} from "@/lib/api";
import type { PipelineRunItem, WipeBdDataResponse } from "@/lib/types";

import { RegenProgress, type PhaseSnapshot } from "./regen-progress";

// Modal that owns the entire Fresh Regen state machine. With the Files
// API toggle ON (default), four phases chain server-side:
//   0. files_api  → POST /api/v1/pipeline/set-files-api-flag (~60-90s
//                   for the BE Cloud Run revision rollout)
//   1. wipe        → POST /api/v1/pipeline/wipe-bd-data
//   2. initial_load → POST /api/v1/pipeline/run/initial-load + poll
//   3. populate_all → POST /api/v1/pipeline/run/populate-all + poll
//
// When the admin unchecks the toggle, Phase 0 is skipped and we run the
// legacy three-phase flow against the BE's existing local-PDF-cache
// path. Polling reuses the existing /api/v1/pipeline/clearing endpoint
// and looks up our run in `recent_runs` by id; we don't introduce a new
// per-run-status endpoint to keep the BE surface unchanged. 30s cadence
// is comfortable for runs that take 15-90 minutes and well under any
// practical rate limit.

interface FreshRegenConfirmModalProps {
  onClose: () => void;
  onSuccess?: () => void;
}

type Stage =
  | "typing"
  | "files_api_flipping"
  | "wiping"
  | "initial_load_pending"
  | "initial_load_running"
  | "populate_pending"
  | "populate_running"
  | "done"
  | "failed";

const POLL_INTERVAL_MS = 30_000;

const TERMINAL_SUCCESS_STATUSES = new Set(["completed", "success"]);
const TERMINAL_FAILURE_STATUSES = new Set(["failed", "error"]);

function todayUtc(): string {
  // YYYY-MM-DD in UTC. Matches what cli01 BE expects in the
  // confirmation string. If the user's clock is off, the BE rejection
  // surfaces the actual-vs-expected strings inline.
  return new Date().toISOString().slice(0, 10);
}

function buildExpectedConfirmation(): string {
  return `WIPE-BD-DATA-${todayUtc()}`;
}

export function FreshRegenConfirmModal({
  onClose,
  onSuccess
}: FreshRegenConfirmModalProps) {
  const expected = useMemo(buildExpectedConfirmation, []);
  const [stage, setStage] = useState<Stage>("typing");
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Streaming Files API path defaults ON: it eliminates the manual
  // `gcloud run services update` flag flip Arvin previously had to run
  // before the regen, and it streams PDFs straight to Gemini's Files
  // API instead of rebuilding the local cache. Admins can opt out by
  // unchecking the toggle in the typing stage if Cloud Run rollout
  // keeps timing out (Phase 0 503), or to keep the legacy ingestion
  // path for any other reason.
  const [useFilesApi, setUseFilesApi] = useState(true);
  const [filesApiResult, setFilesApiResult] =
    useState<SetFilesApiFlagResponse | null>(null);
  const [wipeResult, setWipeResult] = useState<WipeBdDataResponse | null>(null);
  const [initialLoadRunId, setInitialLoadRunId] = useState<number | null>(null);
  const [populateRunId, setPopulateRunId] = useState<number | null>(null);
  const [initialLoadProgress, setInitialLoadProgress] =
    useState<PipelineRunItem | null>(null);
  const [populateProgress, setPopulateProgress] =
    useState<PipelineRunItem | null>(null);

  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const isMountedRef = useRef(true);
  const onSuccessRef = useRef(onSuccess);

  useEffect(() => {
    onSuccessRef.current = onSuccess;
  }, [onSuccess]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Focus the input on first paint when the user is in the typing
  // stage. Once a phase is in flight there's nothing meaningful to
  // type, so we don't keep re-focusing.
  useEffect(() => {
    if (stage === "typing") {
      inputRef.current?.focus();
    }
  }, [stage]);

  const inFlight =
    stage === "files_api_flipping" ||
    stage === "wiping" ||
    stage === "initial_load_pending" ||
    stage === "initial_load_running" ||
    stage === "populate_pending" ||
    stage === "populate_running";

  // Esc dismiss is allowed in typing / done / failed states. Once the
  // run is in flight, the user has to click the explicit "close
  // (continues server-side)" link — we don't want a stray Esc to dump
  // the polling indicator and leave them wondering.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (inFlight) return;
      onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [inFlight, onClose]);

  // ── Phase 2: poll initial_load until terminal ──────────────────────
  useEffect(() => {
    if (stage !== "initial_load_running" || initialLoadRunId === null) {
      return;
    }

    let cancelled = false;

    async function tick() {
      try {
        const run = await findPipelineRun(initialLoadRunId as number);
        if (cancelled || !isMountedRef.current) return;
        if (run) {
          setInitialLoadProgress(run);
          const status = run.status.toLowerCase();
          if (TERMINAL_SUCCESS_STATUSES.has(status)) {
            setStage("populate_pending");
            return;
          }
          if (TERMINAL_FAILURE_STATUSES.has(status)) {
            setError(
              `Initial load failed (run #${run.id}). Check recent runs for details.`
            );
            setStage("failed");
            return;
          }
        }
      } catch (pollError) {
        // Don't fail the whole flow on a transient poll error — the
        // run may still complete server-side. Surface the message in
        // the phase detail so the user knows polling is degraded, but
        // keep ticking.
        if (!cancelled && isMountedRef.current) {
          const message = errorMessage(pollError);
          setInitialLoadProgress((current) =>
            current
              ? { ...current, notes: `Polling error: ${message}` }
              : current
          );
        }
      }
    }

    void tick();
    const interval = setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [stage, initialLoadRunId]);

  // ── Phase 3: kick off populate_all once initial_load is done ───────
  useEffect(() => {
    if (stage !== "populate_pending") return;

    let cancelled = false;
    (async () => {
      try {
        const response = await runPopulateAll();
        if (cancelled || !isMountedRef.current) return;
        setPopulateRunId(response.run_id);
        setStage("populate_running");
      } catch (kickoffError) {
        if (cancelled || !isMountedRef.current) return;
        setError(errorMessage(kickoffError));
        setStage("failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [stage]);

  // ── Phase 3 (cont.): poll populate_all until terminal ──────────────
  useEffect(() => {
    if (stage !== "populate_running" || populateRunId === null) {
      return;
    }

    let cancelled = false;

    async function tick() {
      try {
        const run = await findPipelineRun(populateRunId as number);
        if (cancelled || !isMountedRef.current) return;
        if (run) {
          setPopulateProgress(run);
          const status = run.status.toLowerCase();
          if (TERMINAL_SUCCESS_STATUSES.has(status)) {
            setStage("done");
            onSuccessRef.current?.();
            return;
          }
          if (TERMINAL_FAILURE_STATUSES.has(status)) {
            setError(
              `Populate-all failed (run #${run.id}). Check recent runs for details.`
            );
            setStage("failed");
            return;
          }
        }
      } catch (pollError) {
        if (!cancelled && isMountedRef.current) {
          const message = errorMessage(pollError);
          setPopulateProgress((current) =>
            current
              ? { ...current, notes: `Polling error: ${message}` }
              : current
          );
        }
      }
    }

    void tick();
    const interval = setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [stage, populateRunId]);

  async function handleSubmit() {
    if (typed !== expected || stage !== "typing") return;

    setError(null);

    // Phase 0 — flip LLM_USE_FILES_API and wait for the BE Cloud Run
    // revision to roll out. Skipped when the admin opted out via the
    // toggle, keeping today's three-phase legacy flow intact.
    if (useFilesApi) {
      setStage("files_api_flipping");
      try {
        const flagResponse = await setFilesApiFlag(true);
        if (!isMountedRef.current) return;
        setFilesApiResult(flagResponse);
      } catch (flagError) {
        if (!isMountedRef.current) return;
        setStage("typing");
        setError(buildFilesApiErrorMessage(flagError));
        return;
      }
    }

    setStage("wiping");

    try {
      const wipeResponse = await wipeBdData(typed);
      setWipeResult(wipeResponse);
      setStage("initial_load_pending");
      const initialLoadResponse = await runInitialLoad();
      if (!isMountedRef.current) return;
      setInitialLoadRunId(initialLoadResponse.run_id);
      setStage("initial_load_running");
    } catch (wipeError) {
      if (!isMountedRef.current) return;
      setStage("typing");
      setError(buildWipeErrorMessage(wipeError, expected));
    }
  }

  const phases = buildPhases({
    stage,
    expected,
    useFilesApi,
    filesApiResult,
    wipeResult,
    initialLoadRunId,
    initialLoadProgress,
    populateRunId,
    populateProgress
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="fresh-regen-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (!inFlight) onClose();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[520px] rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <h2
          id="fresh-regen-title"
          className="text-lg font-semibold tracking-tight text-navy"
        >
          {stage === "done" ? "Regen complete" : "Confirm Fresh Regen"}
        </h2>

        {stage === "typing" || stage === "failed" ? (
          <TypingBody
            expected={expected}
            typed={typed}
            onTypedChange={setTyped}
            inputRef={inputRef}
            error={error}
            stage={stage}
            useFilesApi={useFilesApi}
            onUseFilesApiChange={setUseFilesApi}
          />
        ) : null}

        {stage !== "typing" ? <RegenProgress phases={phases} /> : null}

        {stage === "done" ? (
          <DoneBody
            wipeResult={wipeResult}
            initialLoadRunId={initialLoadRunId}
            populateRunId={populateRunId}
          />
        ) : null}

        <ModalActions
          stage={stage}
          typed={typed}
          expected={expected}
          inFlight={inFlight}
          onCancel={onClose}
          onSubmit={handleSubmit}
          cancelRef={cancelRef}
        />
      </div>
    </div>
  );
}

function TypingBody({
  expected,
  typed,
  onTypedChange,
  inputRef,
  error,
  stage,
  useFilesApi,
  onUseFilesApiChange
}: {
  expected: string;
  typed: string;
  onTypedChange: (value: string) => void;
  inputRef: React.MutableRefObject<HTMLInputElement | null>;
  error: string | null;
  stage: Stage;
  useFilesApi: boolean;
  onUseFilesApiChange: (value: boolean) => void;
}) {
  return (
    <div className="mt-3 space-y-4">
      <p className="text-sm leading-6 text-slate-700">
        This wipes <span className="font-semibold">all</span> broker-dealer
        data (~3,002 firms) and re-fetches them from FINRA + SEC. Cannot be
        undone. ~1–2 hours wall-clock.
      </p>
      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
          Type the exact string below to proceed
        </p>
        <code className="block w-full select-all rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-sm tracking-wide text-navy">
          {expected}
        </code>
        <input
          ref={inputRef}
          type="text"
          value={typed}
          onChange={(event) => onTypedChange(event.target.value)}
          placeholder={expected}
          autoComplete="off"
          spellCheck={false}
          className="block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20"
        />
      </div>
      <div className="rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-3">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={useFilesApi}
            onChange={(event) => onUseFilesApiChange(event.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-blue focus:ring-blue/30"
          />
          <span className="space-y-1">
            <span className="block text-sm font-medium text-navy">
              Use streaming Files API (recommended)
            </span>
            <span className="block text-xs leading-5 text-slate-600">
              When enabled, the regen flips{" "}
              <code className="font-mono text-[11px] text-slate-700">
                LLM_USE_FILES_API=true
              </code>{" "}
              and rolls out a new backend revision (~60–90s) before
              wiping. PDFs stream to Gemini&apos;s Files API instead of
              rebuilding the local cache.
            </span>
          </span>
        </label>
      </div>
      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : stage === "failed" ? (
        <p className="text-xs text-slate-500">
          Adjust the input above and submit again to retry.
        </p>
      ) : null}
    </div>
  );
}

function DoneBody({
  wipeResult,
  initialLoadRunId,
  populateRunId
}: {
  wipeResult: WipeBdDataResponse | null;
  initialLoadRunId: number | null;
  populateRunId: number | null;
}) {
  return (
    <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm text-slate-700">
      <p className="font-medium text-navy">
        Fresh regen finished end-to-end.
      </p>
      <ul className="mt-2 space-y-1 text-xs text-slate-600">
        {wipeResult ? (
          <li>
            Wipe audit log id:{" "}
            <span className="font-mono text-navy">
              #{wipeResult.audit_log_id}
            </span>{" "}
            ({wipeResult.rows_deleted.toLocaleString()} rows across{" "}
            {wipeResult.affected_tables.length} tables)
          </li>
        ) : null}
        {initialLoadRunId !== null ? (
          <li>
            Initial load run id:{" "}
            <span className="font-mono text-navy">#{initialLoadRunId}</span>
          </li>
        ) : null}
        {populateRunId !== null ? (
          <li>
            Populate-all run id:{" "}
            <span className="font-mono text-navy">#{populateRunId}</span>
          </li>
        ) : null}
      </ul>
      <p className="mt-2 text-xs text-slate-500">
        See the recent-runs table below for the full history.
      </p>
    </div>
  );
}

function ModalActions({
  stage,
  typed,
  expected,
  inFlight,
  onCancel,
  onSubmit,
  cancelRef
}: {
  stage: Stage;
  typed: string;
  expected: string;
  inFlight: boolean;
  onCancel: () => void;
  onSubmit: () => void;
  cancelRef: React.MutableRefObject<HTMLButtonElement | null>;
}) {
  if (stage === "done") {
    return (
      <div className="mt-6 flex items-center justify-end">
        <button
          ref={cancelRef}
          type="button"
          onClick={onCancel}
          className="inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54]"
        >
          Close
        </button>
      </div>
    );
  }

  if (inFlight) {
    return (
      <div className="mt-6 flex flex-col items-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="text-xs font-medium text-slate-500 underline-offset-4 transition hover:text-slate-700 hover:underline"
        >
          Close (regen continues server-side)
        </button>
        <p className="text-[11px] text-slate-400">
          Closing only stops live updates here. The backend keeps running.
        </p>
      </div>
    );
  }

  const submitDisabled = typed !== expected;
  return (
    <div className="mt-6 flex items-center justify-end gap-3">
      <button
        ref={cancelRef}
        type="button"
        onClick={onCancel}
        className="inline-flex h-10 items-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={onSubmit}
        disabled={submitDisabled}
        className="inline-flex h-10 items-center rounded-xl bg-danger px-4 text-sm font-semibold text-white shadow-lg shadow-red-300/40 transition hover:bg-[#c62a2a] disabled:cursor-not-allowed disabled:opacity-60"
      >
        {stage === "failed" ? "Retry Fresh Regen" : "Start Fresh Regen"}
      </button>
    </div>
  );
}

function buildPhases(args: {
  stage: Stage;
  expected: string;
  useFilesApi: boolean;
  filesApiResult: SetFilesApiFlagResponse | null;
  wipeResult: WipeBdDataResponse | null;
  initialLoadRunId: number | null;
  initialLoadProgress: PipelineRunItem | null;
  populateRunId: number | null;
  populateProgress: PipelineRunItem | null;
}): PhaseSnapshot[] {
  const {
    stage,
    expected,
    useFilesApi,
    filesApiResult,
    wipeResult,
    initialLoadRunId,
    initialLoadProgress,
    populateRunId,
    populateProgress
  } = args;

  const filesApiPhase: PhaseSnapshot = {
    id: "files_api",
    label: "Phase 0 — Enable Files API path (~60–90s)",
    status: phaseStatusFor("files_api", stage),
    detail: filesApiResult
      ? `Revision ${filesApiResult.revision_name} ready (LLM_USE_FILES_API ${filesApiResult.previous_state} → ${filesApiResult.new_state})`
      : stage === "files_api_flipping"
        ? "Rolling out new backend revision — please wait, this is not hung."
        : undefined
  };

  const wipePhase: PhaseSnapshot = {
    id: "wipe",
    label: "Phase 1 — Wipe BD data",
    status: phaseStatusFor("wipe", stage),
    detail: wipeResult
      ? `${wipeResult.rows_deleted.toLocaleString()} rows across ${wipeResult.affected_tables.length} tables (audit #${wipeResult.audit_log_id})`
      : stage === "wiping"
        ? `POSTing confirmation ${expected}…`
        : undefined
  };

  const initialPhase: PhaseSnapshot = {
    id: "initial_load",
    label: "Phase 2 — Initial Load (~15-30 min)",
    status: phaseStatusFor("initial_load", stage),
    detail: detailForRun(initialLoadRunId, initialLoadProgress)
  };

  const populatePhase: PhaseSnapshot = {
    id: "populate_all",
    label: "Phase 3 — Populate All Data (~30-90 min)",
    status: phaseStatusFor("populate_all", stage),
    detail: detailForRun(populateRunId, populateProgress)
  };

  return useFilesApi
    ? [filesApiPhase, wipePhase, initialPhase, populatePhase]
    : [wipePhase, initialPhase, populatePhase];
}

function phaseStatusFor(
  phase: "files_api" | "wipe" | "initial_load" | "populate_all",
  stage: Stage
): PhaseSnapshot["status"] {
  if (phase === "files_api") {
    if (stage === "typing") return "pending";
    if (stage === "files_api_flipping") return "running";
    // Phase 0 only fails by snapping the modal back to "typing" with an
    // inline error, so any later stage means Phase 0 succeeded.
    return "done";
  }
  if (phase === "wipe") {
    if (stage === "typing" || stage === "files_api_flipping") return "pending";
    if (stage === "wiping") return "running";
    if (stage === "failed") return "failed";
    return "done";
  }
  if (phase === "initial_load") {
    if (
      stage === "typing" ||
      stage === "files_api_flipping" ||
      stage === "wiping"
    )
      return "pending";
    if (stage === "initial_load_pending" || stage === "initial_load_running")
      return "running";
    if (stage === "failed") {
      return "failed";
    }
    return "done";
  }
  // populate_all
  if (stage === "populate_pending" || stage === "populate_running")
    return "running";
  if (stage === "done") return "done";
  if (stage === "failed") return "failed";
  return "pending";
}

function detailForRun(
  runId: number | null,
  progress: PipelineRunItem | null
): string | undefined {
  if (runId === null) return undefined;
  if (!progress) return `Run #${runId} — waiting for first status…`;
  const ratio =
    progress.total_items > 0
      ? Math.round((progress.processed_items / progress.total_items) * 100)
      : null;
  const pct = ratio !== null ? ` · ${ratio}%` : "";
  return `Run #${runId} · ${progress.status}${pct} (${progress.processed_items}/${progress.total_items})`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

function buildWipeErrorMessage(error: unknown, expected: string): string {
  if (error instanceof ApiError && error.status === 400) {
    return `Backend rejected the confirmation. Expected: ${expected}. ${error.detail}`;
  }
  if (error instanceof ApiError && error.status === 403) {
    return "You don't have permission to run a fresh regen.";
  }
  return errorMessage(error);
}

function buildFilesApiErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "Could not enable Files API (Cloud Run rollout timed out). Try again, or proceed without streaming by unchecking the toggle.";
  }
  if (error instanceof ApiError && error.status === 403) {
    return "Admin access required.";
  }
  if (error instanceof ApiError) {
    return `Failed to enable Files API: ${error.detail}`;
  }
  return errorMessage(error);
}
