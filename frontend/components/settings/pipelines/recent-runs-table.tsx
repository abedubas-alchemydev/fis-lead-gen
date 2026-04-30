"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiRequest } from "@/lib/api";
import type {
  PipelineRunItem,
  PipelineStatusResponse,
} from "@/lib/types";

const MAX_ROWS = 5;

interface RecentRunsTableProps {
  // Bumped by the parent after a successful trigger so this table
  // re-fetches without prop-drilling state. Default 0 = initial load only.
  refreshKey?: number;
}

export function RecentRunsTable({ refreshKey = 0 }: RecentRunsTableProps) {
  const [runs, setRuns] = useState<PipelineRunItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const status = await apiRequest<PipelineStatusResponse>(
        "/api/v1/pipeline/clearing",
      );
      // recent_runs is already ordered newest-first by the BE.
      setRuns(status.recent_runs.slice(0, MAX_ROWS));
      setError(null);
    } catch (loadError) {
      const message =
        loadError instanceof ApiError
          ? loadError.detail
          : loadError instanceof Error
            ? loadError.message
            : "Unable to load recent pipeline runs.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  return (
    <section className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            Recent runs
          </p>
          <h2 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Pipeline activity
          </h2>
          <p className="mt-1 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Latest {MAX_ROWS} pipeline runs across all triggers (manual or
            scheduled).
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? (
        <p className="mt-4 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </p>
      ) : null}

      <div className="mt-5 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
        <table className="w-full text-left text-sm">
          <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            <tr>
              <th className="px-4 py-3">Pipeline</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Processed</th>
              <th className="px-4 py-3">Trigger</th>
              <th className="px-4 py-3">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs && runs.length > 0 ? (
              runs.map((run) => (
                <tr
                  key={run.id}
                  className="border-t border-[var(--border,rgba(30,64,175,0.1))] text-[var(--text-dim,#475569)]"
                >
                  <td className="px-4 py-3 font-mono text-[12px] text-[var(--text,#0f172a)]">
                    {run.pipeline_name}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-3 text-[var(--text-dim,#475569)]">
                    {run.processed_items}/{run.total_items}
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--text-muted,#94a3b8)]">
                    <span
                      className="block max-w-[200px] truncate"
                      title={run.trigger_source}
                    >
                      {run.trigger_source}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-[var(--text-muted,#94a3b8)]">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-8 text-center text-sm text-[var(--text-muted,#94a3b8)]"
                >
                  {loading
                    ? "Loading recent runs…"
                    : "No pipeline runs recorded yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type RunStatus = "running" | "completed" | "failed" | "idle";

const STATUS_STYLE: Record<
  RunStatus,
  { label: string; pill: string; dot: string; pulse: boolean }
> = {
  running: {
    label: "Running",
    pill: "bg-blue-500/12 text-[var(--pill-blue-text,#1d4ed8)] border-blue-500/25",
    dot: "bg-blue-500",
    pulse: true,
  },
  completed: {
    label: "Completed",
    pill: "bg-emerald-500/12 text-[var(--pill-green-text,#047857)] border-emerald-500/25",
    dot: "bg-emerald-500",
    pulse: false,
  },
  failed: {
    label: "Failed",
    pill: "bg-red-500/12 text-[var(--pill-red-text,#b91c1c)] border-red-500/25",
    dot: "bg-red-500",
    pulse: false,
  },
  idle: {
    label: "Idle",
    pill: "bg-slate-100 text-slate-600 border-slate-200",
    dot: "bg-slate-400",
    pulse: false,
  },
};

function resolveStatus(raw: string): RunStatus {
  const n = raw.toLowerCase();
  if (n === "completed" || n === "success") return "completed";
  if (n === "failed" || n === "error") return "failed";
  if (n === "running" || n === "in_progress") return "running";
  return "idle";
}

function StatusBadge({ status }: { status: string }) {
  const key = resolveStatus(status);
  const style = STATUS_STYLE[key];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] ${style.pill}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${style.dot} ${
          style.pulse ? "animate-pulse" : ""
        }`}
        aria-hidden
      />
      {style.label}
    </span>
  );
}
