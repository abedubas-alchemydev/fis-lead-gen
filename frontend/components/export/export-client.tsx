"use client";

import { useEffect, useMemo, useState } from "react";

import { Download } from "lucide-react";

import { TopActions } from "@/components/layout/top-actions";
import { SectionPanel } from "@/components/ui/section-panel";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";
import { apiRequest, buildApiPath } from "@/lib/api";
import type { ExportCsvResponse, ExportPreviewResponse } from "@/lib/types";

type ListMode = "primary" | "alternative" | "all";

// Filter option catalogs — module-level so the arrays are referentially
// stable between renders (mirrors master-list-workspace-client / alerts-client).
const LIST_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "primary", label: "Primary" },
  { value: "alternative", label: "Alternative" },
  { value: "all", label: "All firms" },
];

const PRIORITY_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "hot", label: "Hot", dot: "hot" },
  { value: "warm", label: "Warm", dot: "warm" },
  { value: "cold", label: "Cold", dot: "cold" },
];

const HEALTH_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "All", label: "All" },
  { value: "healthy", label: "Healthy", dot: "healthy" },
  { value: "ok", label: "OK", dot: "ok" },
  { value: "at_risk", label: "At Risk", dot: "risk" },
];

export function ExportClient({
  initialListMode = "primary",
}: {
  initialListMode?: ListMode;
}) {
  const [listMode, setListMode] = useState<ListMode>(initialListMode);
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
        health: health === "All" ? undefined : [health],
      }),
    [listMode, leadPriority, health],
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
          health: health === "All" ? undefined : [health],
        }),
        { method: "POST" },
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

  function clearFilters() {
    setListMode("primary");
    setLeadPriority("All");
    setHealth("All");
  }

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (listMode !== "primary") count += 1;
    if (leadPriority !== "All") count += 1;
    if (health !== "All") count += 1;
    return count;
  }, [listMode, leadPriority, health]);

  const remainingExports = preview?.remaining_exports_today ?? null;
  const matchingRecords = preview?.matching_records ?? null;
  const requestedRecords = preview?.requested_records ?? null;
  const quotaExhausted = remainingExports !== null && remainingExports <= 0;

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ───────────────────────────────────────────────────────── */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Export
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Restricted CSV export
          </h1>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-[3px] text-[11px] font-semibold ${
              quotaExhausted
                ? "border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] text-[var(--pill-red-text,#b91c1c)]"
                : "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]"
            }`}
          >
            {remainingExports === null
              ? "— of 3 exports remaining today"
              : `${remainingExports} of 3 export${remainingExports === 1 ? "" : "s"} remaining today`}
          </span>
          <TopActions />
        </div>
      </div>

      {/* ── Live-match strip ─────────────────────────────────────────────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {matchingRecords === null
            ? "Loading…"
            : `${matchingRecords.toLocaleString()} match${matchingRecords === 1 ? "" : "es"}`}
        </span>
        <span>Each export ships up to 100 rows of permitted fields.</span>
      </div>

      {/* ── Filters card ─────────────────────────────────────────────────── */}
      <div
        className="mb-4 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
        style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
      >
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Filters
              {activeFilterCount > 0 ? (
                <span className="rounded-full bg-[rgba(99,102,241,0.12)] px-2 py-0.5 text-[10px] font-bold tracking-[0.04em] text-[#4338ca]">
                  {activeFilterCount} ACTIVE
                </span>
              ) : null}
            </p>
            <h3 className="mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Refine the export
            </h3>
          </div>
          <button
            type="button"
            onClick={clearFilters}
            className="rounded-[6px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
          >
            Clear filters
          </button>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              List
            </p>
            <Segmented
              value={listMode}
              onChange={(next) => setListMode(next as ListMode)}
              items={LIST_ITEMS}
              ariaLabel="List mode"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Lead Priority
            </p>
            <Segmented
              value={leadPriority}
              onChange={setLeadPriority}
              items={PRIORITY_ITEMS}
              ariaLabel="Lead priority"
            />
          </div>
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Financial Health
            </p>
            <Segmented
              value={health}
              onChange={setHealth}
              items={HEALTH_ITEMS}
              ariaLabel="Financial health"
            />
          </div>
        </div>
      </div>

      {error ? (
        <div className="mb-4 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {/* ── Preview + Rules grid ─────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <SectionPanel eyebrow="Preview" title="Selection summary">
          <div className="grid gap-3">
            <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                Matching records
              </p>
              <p className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
                {matchingRecords === null ? "—" : matchingRecords.toLocaleString()}
              </p>
            </div>
            <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                Exported this run
              </p>
              <p className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
                {requestedRecords === null ? "—" : requestedRecords.toLocaleString()}
              </p>
            </div>
            <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                Remaining today
              </p>
              <p className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
                {remainingExports === null ? "—" : remainingExports.toLocaleString()}
              </p>
            </div>
          </div>
        </SectionPanel>

        <SectionPanel eyebrow="Restricted CSV" title="Export rules">
          <ul className="grid gap-2.5">
            <li className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] text-[var(--text-dim,#475569)]">
              Only permitted CSV fields are exported.
            </li>
            <li className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] text-[var(--text-dim,#475569)]">
              Names may export, but email, phone, and LinkedIn never do.
            </li>
            <li className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] text-[var(--text-dim,#475569)]">
              Each file includes a source watermark footer.
            </li>
            <li className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] text-[var(--text-dim,#475569)]">
              Export volume is capped to keep teams in-platform.
            </li>
          </ul>
          <div className="mt-5 flex flex-col gap-2 border-t border-dashed border-[var(--border,rgba(30,64,175,0.1))] pt-4">
            <button
              type="button"
              onClick={() => void exportCsv()}
              disabled={isExporting || quotaExhausted}
              className="inline-flex w-fit items-center gap-2 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:brightness-100"
            >
              <Download className="h-4 w-4" strokeWidth={2} />
              {isExporting ? "Preparing CSV…" : "Export CSV"}
            </button>
            <p className="text-[11px] text-[var(--text-muted,#94a3b8)]">
              Up to 100 rows · 9 permitted columns · resets at midnight UTC.
            </p>
          </div>
        </SectionPanel>
      </div>
    </div>
  );
}
