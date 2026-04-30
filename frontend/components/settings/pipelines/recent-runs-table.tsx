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
    <section className="rounded-[30px] border border-white/80 bg-white/92 p-7 shadow-shell">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-blue">
            Recent runs
          </p>
          <p className="mt-2 text-sm text-slate-600">
            Latest {MAX_ROWS} pipeline runs across all triggers (manual or
            scheduled).
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center rounded-xl border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? (
        <p className="mt-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : null}

      <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-[0.18em] text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">Pipeline</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Processed</th>
              <th className="px-4 py-3 font-medium">Trigger</th>
              <th className="px-4 py-3 font-medium">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs && runs.length > 0 ? (
              runs.map((run) => (
                <tr
                  key={run.id}
                  className="border-t border-slate-200 text-slate-700"
                >
                  <td className="px-4 py-3 font-medium text-navy">
                    {run.pipeline_name}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {run.processed_items}/{run.total_items}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {run.trigger_source}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-8 text-center text-sm text-slate-500"
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

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const palette =
    normalized === "completed" || normalized === "success"
      ? "bg-emerald-50 text-success border-emerald-100"
      : normalized === "failed" || normalized === "error"
        ? "bg-red-50 text-danger border-red-100"
        : normalized === "running" || normalized === "in_progress"
          ? "bg-blue/10 text-blue border-blue/20"
          : "bg-slate-100 text-slate-600 border-slate-200";

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium capitalize ${palette}`}
    >
      {status}
    </span>
  );
}
