"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { apiRequest } from "@/lib/api";
import type { TimeSeriesBucket, TimeSeriesRange, TimeSeriesResponse } from "@/lib/types";

// ── Chart geometry ────────────────────────────────────────────────────────
// The viewBox is sized to the container's actual pixel dimensions via a
// ResizeObserver, so SVG units map 1:1 to CSS pixels. That removes the
// `preserveAspectRatio="none"` stretching that used to warp strokes + shapes
// whenever the row grew taller than the original 220px baseline.
const CHART_TOP_PAD = 20; // breathing room above the highest point
const CHART_LABEL_PAD = 20; // leaves room for the date labels below
const LABEL_BOTTOM_OFFSET = 8; // gap between chart floor and label baseline

// Grid-line fractions mirror the mockup's y=40/90/140/190 over a 220-unit
// viewBox — kept as ratios so they re-compute cleanly at any height.
const GRID_FRACTIONS = [40 / 220, 90 / 220, 140 / 220, 190 / 220] as const;

const RANGES: ReadonlyArray<TimeSeriesRange> = ["7D", "30D", "90D", "1Y"];

type Size = { width: number; height: number };

type SeriesGeometry = {
  linePath: string;
  areaPath: string;
  lastPoint: { x: number; y: number } | null;
};

function buildSeriesPaths(
  buckets: ReadonlyArray<TimeSeriesBucket>,
  pick: (bucket: TimeSeriesBucket) => number,
  yMax: number,
  width: number,
  height: number,
): SeriesGeometry {
  if (buckets.length === 0 || width <= 0 || height <= 0) {
    return { linePath: "", areaPath: "", lastPoint: null };
  }
  const denom = yMax > 0 ? yMax : 1;
  const chartBottom = height - CHART_LABEL_PAD;
  const usable = Math.max(1, chartBottom - CHART_TOP_PAD);
  const step = buckets.length === 1 ? 0 : width / (buckets.length - 1);

  const points = buckets.map((bucket, idx) => {
    const x = step * idx;
    const y = chartBottom - (pick(bucket) / denom) * usable;
    return { x, y };
  });

  const linePath = points
    .map((p, idx) => `${idx === 0 ? "M" : "L"}${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(" ");
  const last = points[points.length - 1];
  const areaPath = `${linePath} L${last.x.toFixed(2)} ${height} L0 ${height} Z`;

  return { linePath, areaPath, lastPoint: last };
}

function formatShortDate(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00Z`);
  return parsed.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

type AxisLabel = { x: number; label: string; anchor: "start" | "middle" | "end" };

function pickAxisLabels(
  buckets: ReadonlyArray<TimeSeriesBucket>,
  width: number,
): AxisLabel[] {
  if (buckets.length === 0 || width <= 0) return [];
  const n = buckets.length;
  const step = n === 1 ? 0 : width / (n - 1);
  const indices =
    n <= 5
      ? Array.from({ length: n }, (_, i) => i)
      : [0, Math.round((n - 1) * 0.25), Math.round((n - 1) * 0.5), Math.round((n - 1) * 0.75), n - 1];

  return indices.map((i, idx, arr) => {
    const anchor: AxisLabel["anchor"] =
      idx === 0 ? "start" : idx === arr.length - 1 ? "end" : "middle";
    return { x: step * i, label: formatShortDate(buckets[i].date), anchor };
  });
}

export function LeadVolumeTrendCard() {
  const [range, setRange] = useState<TimeSeriesRange>("30D");
  const [buckets, setBuckets] = useState<TimeSeriesBucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Measure the chart container so the viewBox can mirror actual pixel
  // dimensions — this is what keeps strokes un-stretched when the card
  // grows taller to match the TopLeadsCard row height.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const update = () => {
      const rect = node.getBoundingClientRect();
      setSize((prev) =>
        prev.width === rect.width && prev.height === rect.height
          ? prev
          : { width: rect.width, height: rect.height },
      );
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    apiRequest<TimeSeriesResponse>(`/api/v1/stats/time-series?range=${range}`)
      .then((resp) => {
        if (!active) return;
        setBuckets(resp.buckets);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Unable to load trend data.");
        setBuckets([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [range]);

  const yMax = useMemo(() => {
    let max = 1;
    for (const bucket of buckets) {
      if (bucket.registrations > max) max = bucket.registrations;
      if (bucket.alerts > max) max = bucket.alerts;
    }
    return max;
  }, [buckets]);

  const registrations = useMemo(
    () => buildSeriesPaths(buckets, (b) => b.registrations, yMax, size.width, size.height),
    [buckets, yMax, size.width, size.height],
  );
  const alerts = useMemo(
    () => buildSeriesPaths(buckets, (b) => b.alerts, yMax, size.width, size.height),
    [buckets, yMax, size.width, size.height],
  );
  const axisLabels = useMemo(
    () => pickAxisLabels(buckets, size.width),
    [buckets, size.width],
  );
  const gridLines = useMemo(
    () => (size.height > 0 ? GRID_FRACTIONS.map((f) => f * size.height) : []),
    [size.height],
  );

  const viewBox = `0 0 ${Math.max(size.width, 1)} ${Math.max(size.height, 1)}`;
  const labelY = Math.max(size.height - LABEL_BOTTOM_OFFSET, 0);
  const ready = size.width > 0 && size.height > 0;

  return (
    // `flex h-full flex-col` lets the chart fill whatever vertical space the
    // grid row hands us (bounded by TopLeadsCard on the right). Themed
    // border/bg/shadow tokens match the KPI cards + mockup `.card` rule.
    <div
      className="flex h-full flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5"
      style={{ boxShadow: "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))" }}
    >
      {/* .card-head: items-center (mockup), not items-start */}
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Lead volume trend
          </h3>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            Registrations vs. deficiency alerts
          </p>
        </div>
        {/* .chips + .chip: 11px, padding 5px 10px, 8px radius, weight 500.
            Active uses verbatim mockup colors (#a5b4fc on 15%-alpha violet). */}
        <div className="flex gap-1.5">
          {RANGES.map((key) => {
            const isActive = key === range;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setRange(key)}
                disabled={loading && isActive}
                className={`rounded-lg border px-2.5 py-[5px] text-[11px] font-medium transition ${
                  isActive
                    ? "border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.15)] text-[#a5b4fc]"
                    : "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-3,#dbeafe)]"
                } ${loading && isActive ? "cursor-progress opacity-70" : ""}`}
              >
                {key}
              </button>
            );
          })}
        </div>
      </div>

      {error ? (
        <div className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      ) : null}

      {/* Chart wrapper grows to fill remaining card height. `min-h-0` lets
          flex-1 actually shrink below the SVG's intrinsic content size. */}
      <div ref={containerRef} className="min-h-0 w-full flex-1">
        <svg className="block h-full w-full" viewBox={viewBox}>
          <defs>
            <linearGradient id="trend-area-a" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#6366f1" stopOpacity="0.45" />
              <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="trend-area-b" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#ef4444" stopOpacity="0.35" />
              <stop offset="100%" stopColor="#ef4444" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ready ? (
            <g stroke="rgba(15, 23, 42, 0.06)" strokeWidth="1">
              {gridLines.map((y, idx) => (
                <line key={idx} x1="0" y1={y} x2={size.width} y2={y} />
              ))}
            </g>
          ) : null}
          {registrations.areaPath ? (
            <path d={registrations.areaPath} fill="url(#trend-area-a)" />
          ) : null}
          {registrations.linePath ? (
            <path
              d={registrations.linePath}
              fill="none"
              stroke="#4f46e5"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {alerts.areaPath ? (
            <path d={alerts.areaPath} fill="url(#trend-area-b)" />
          ) : null}
          {alerts.linePath ? (
            <path
              d={alerts.linePath}
              fill="none"
              stroke="#dc2626"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {registrations.lastPoint ? (
            <circle
              cx={registrations.lastPoint.x}
              cy={registrations.lastPoint.y}
              r="4"
              fill="#4f46e5"
              stroke="#eaf3ff"
              strokeWidth="2"
            />
          ) : null}
          {alerts.lastPoint ? (
            <circle
              cx={alerts.lastPoint.x}
              cy={alerts.lastPoint.y}
              r="4"
              fill="#dc2626"
              stroke="#eaf3ff"
              strokeWidth="2"
            />
          ) : null}
          {ready ? (
            <g fill="#94a3b8" fontSize="10" fontFamily="Inter">
              {axisLabels.map((label, idx) => (
                <text key={idx} x={label.x} y={labelY} textAnchor={label.anchor}>
                  {label.label}
                </text>
              ))}
            </g>
          ) : null}
        </svg>
      </div>

      {/* .legend: 12px, gap 16px, mt 8px, color text-dim. */}
      <div className="mt-2 flex gap-4 text-[12px] text-[var(--text-dim,#475569)]">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: "#4f46e5" }} />
          New BD registrations
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ backgroundColor: "#dc2626" }} />
          Deficiency alerts
        </span>
      </div>
    </div>
  );
}
