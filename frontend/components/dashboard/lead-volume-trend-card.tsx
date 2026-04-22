"use client";

import { useState } from "react";

// Placeholder trend chart — the mockup's two-series area chart. Data is
// synthetic until we expose a time-series endpoint for registrations and
// deficiency alerts. The visual shape matches the mockup exactly so the
// card fits into the dashboard rhythm; swap the paths once the backend
// surfaces real data.

type RangeKey = "7D" | "30D" | "90D" | "1Y";

const RANGES: ReadonlyArray<RangeKey> = ["7D", "30D", "90D", "1Y"];

const SERIES_REGISTRATIONS_PATH =
  "M0 150 C40 140 80 130 120 120 S200 100 240 110 S340 85 380 70 S460 55 500 45";
const SERIES_ALERTS_PATH =
  "M0 180 C40 175 80 170 120 160 S200 140 240 145 S340 125 380 118 S460 100 500 95";

export function LeadVolumeTrendCard() {
  const [range, setRange] = useState<RangeKey>("30D");

  return (
    <div className="rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-slate-900">
            Lead volume trend
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Registrations vs. deficiency alerts
          </p>
        </div>
        <div className="flex gap-1.5">
          {RANGES.map((key) => {
            const isActive = key === range;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setRange(key)}
                className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
                  isActive
                    ? "border-violet-500/30 bg-violet-500/15 text-violet-600"
                    : "border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100"
                }`}
              >
                {key}
              </button>
            );
          })}
        </div>
      </div>

      <svg className="h-[220px] w-full" viewBox="0 0 500 220" preserveAspectRatio="none">
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
        <g stroke="rgba(15, 23, 42, 0.06)" strokeWidth="1">
          <line x1="0" y1="40" x2="500" y2="40" />
          <line x1="0" y1="90" x2="500" y2="90" />
          <line x1="0" y1="140" x2="500" y2="140" />
          <line x1="0" y1="190" x2="500" y2="190" />
        </g>
        <path d={`${SERIES_REGISTRATIONS_PATH} L500 220 L0 220 Z`} fill="url(#trend-area-a)" />
        <path
          d={SERIES_REGISTRATIONS_PATH}
          fill="none"
          stroke="#4f46e5"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path d={`${SERIES_ALERTS_PATH} L500 220 L0 220 Z`} fill="url(#trend-area-b)" />
        <path
          d={SERIES_ALERTS_PATH}
          fill="none"
          stroke="#dc2626"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <circle cx="500" cy="45" r="4" fill="#4f46e5" stroke="#eaf3ff" strokeWidth="2" />
        <circle cx="500" cy="95" r="4" fill="#dc2626" stroke="#eaf3ff" strokeWidth="2" />
        <g fill="#94a3b8" fontSize="10" fontFamily="Inter">
          <text x="0" y="212">Mar 22</text>
          <text x="120" y="212">Mar 29</text>
          <text x="240" y="212">Apr 5</text>
          <text x="360" y="212">Apr 12</text>
          <text x="460" y="212">Apr 22</text>
        </g>
      </svg>

      <div className="mt-2 flex gap-4 text-xs text-slate-600">
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
