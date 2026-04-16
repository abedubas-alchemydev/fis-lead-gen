"use client";

import { useEffect, useMemo, useState } from "react";

import { apiRequest, buildApiPath } from "@/lib/api";
import type { ExportCsvResponse, ExportPreviewResponse } from "@/lib/types";

export function ExportClient({
  initialListMode = "primary"
}: {
  initialListMode?: "primary" | "alternative" | "all";
}) {
  const [listMode, setListMode] = useState<"primary" | "alternative" | "all">(initialListMode);
  const [leadPriority, setLeadPriority] = useState("All");
  const [health, setHealth] = useState("All");
  const [preview, setPreview] = useState<ExportPreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const queryPath = useMemo(
    () =>
      buildApiPath("/api/v1/export/preview", {
        list: listMode,
        lead_priority: leadPriority === "All" ? undefined : [leadPriority],
        health: health === "All" ? undefined : [health]
      }),
    [listMode, leadPriority, health]
  );

  async function loadPreview() {
    try {
      const response = await apiRequest<ExportPreviewResponse>(queryPath);
      setPreview(response);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load export preview.");
    }
  }

  useEffect(() => {
    void loadPreview();
  }, [queryPath]);

  async function exportCsv() {
    setIsExporting(true);
    try {
      const response = await apiRequest<ExportCsvResponse>(
        buildApiPath("/api/v1/export", {
          list: listMode,
          lead_priority: leadPriority === "All" ? undefined : [leadPriority],
          health: health === "All" ? undefined : [health]
        }),
        { method: "POST" }
      );
      const blob = new Blob([response.content], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = response.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      await loadPreview();
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "Unable to export CSV.");
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <section className="space-y-6">
      <div className="rounded-[30px] border border-white/80 bg-white/92 p-8 shadow-shell">
        <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Controlled Export</p>
        <h1 className="mt-3 text-3xl font-semibold text-navy">Restricted CSV export</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
          Exports are intentionally limited: maximum 100 rows, three exports per user per day, and no email,
          phone, or LinkedIn fields leave the platform.
        </p>

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <label className="text-sm font-medium text-slate-700">
            List
            <select
              value={listMode}
              onChange={(event) => setListMode(event.target.value as "primary" | "alternative" | "all")}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
            >
              <option value="primary">Primary List</option>
              <option value="alternative">Alternative List</option>
              <option value="all">All Firms</option>
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Lead Priority
            <select
              value={leadPriority}
              onChange={(event) => setLeadPriority(event.target.value)}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
            >
              <option value="All">All</option>
              <option value="hot">Hot</option>
              <option value="warm">Warm</option>
              <option value="cold">Cold</option>
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Health
            <select
              value={health}
              onChange={(event) => setHealth(event.target.value)}
              className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
            >
              <option value="All">All</option>
              <option value="healthy">Healthy</option>
              <option value="ok">OK</option>
              <option value="at_risk">At Risk</option>
            </select>
          </label>
        </div>
      </div>

      {error ? <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">{error}</div> : null}

      <div className="grid gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Preview</p>
          <div className="mt-4 grid gap-3">
            <div className="rounded-2xl bg-slate-50 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Matching records</p>
              <p className="mt-2 text-2xl font-semibold text-navy">{preview?.matching_records ?? "-"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Exported this run</p>
              <p className="mt-2 text-2xl font-semibold text-navy">{preview?.requested_records ?? "-"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Remaining today</p>
              <p className="mt-2 text-2xl font-semibold text-navy">{preview?.remaining_exports_today ?? "-"}</p>
            </div>
          </div>
        </div>

        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Export Rules</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">Only permitted CSV fields are exported.</div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">Names may export, but email, phone, and LinkedIn never do.</div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">Each file includes a source watermark footer.</div>
            <div className="rounded-2xl bg-slate-50 px-4 py-4 text-sm text-slate-600">Export volume is capped to keep teams in-platform.</div>
          </div>
          <button
            type="button"
            onClick={() => void exportCsv()}
            disabled={isExporting || (preview?.remaining_exports_today ?? 0) <= 0}
            className="mt-6 rounded-2xl bg-navy px-5 py-3 text-sm font-medium text-white disabled:opacity-60"
          >
            {isExporting ? "Preparing CSV..." : "Export CSV"}
          </button>
        </div>
      </div>
    </section>
  );
}
