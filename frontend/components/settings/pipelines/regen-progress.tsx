"use client";

import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

// Three-phase progress display rendered inside the Fresh Regen modal.
// Kept generic over the array of phases so the modal can drive state
// transitions and this component stays a pure render concern. Status
// values mirror the FE state machine (idle/pending stay as "pending"
// here — the modal swaps them to "running" / "done" / "failed" as the
// chained calls progress).

export type PhaseStatus = "pending" | "running" | "done" | "failed";

export interface PhaseSnapshot {
  id: string;
  label: string;
  status: PhaseStatus;
  // Optional supporting line shown under the label, e.g. ETA, run id,
  // or BE error message. Kept free-form so the modal can localize per
  // phase without the progress component knowing the domain.
  detail?: string;
}

interface RegenProgressProps {
  phases: PhaseSnapshot[];
}

export function RegenProgress({ phases }: RegenProgressProps) {
  return (
    <ol className="mt-5 space-y-3" aria-label="Fresh regen progress">
      {phases.map((phase) => (
        <PhaseRow key={phase.id} phase={phase} />
      ))}
    </ol>
  );
}

function PhaseRow({ phase }: { phase: PhaseSnapshot }) {
  const palette = paletteFor(phase.status);
  return (
    <li
      className={`flex items-start gap-3 rounded-2xl border px-4 py-3 ${palette.surface}`}
    >
      <div className={`mt-0.5 ${palette.icon}`}>
        <PhaseIcon status={phase.status} />
      </div>
      <div className="flex-1">
        <p className={`text-sm font-medium ${palette.label}`}>{phase.label}</p>
        {phase.detail ? (
          <p className={`mt-0.5 text-xs ${palette.detail}`}>{phase.detail}</p>
        ) : null}
      </div>
      <span
        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.16em] ${palette.badge}`}
      >
        {labelFor(phase.status)}
      </span>
    </li>
  );
}

function PhaseIcon({ status }: { status: PhaseStatus }) {
  const props = { className: "h-4 w-4", "aria-hidden": true } as const;
  if (status === "running") {
    return <Loader2 {...props} className="h-4 w-4 animate-spin" />;
  }
  if (status === "done") {
    return <CheckCircle2 {...props} />;
  }
  if (status === "failed") {
    return <XCircle {...props} />;
  }
  return <Circle {...props} />;
}

function labelFor(status: PhaseStatus): string {
  switch (status) {
    case "running":
      return "In progress";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    default:
      return "Pending";
  }
}

function paletteFor(status: PhaseStatus) {
  switch (status) {
    case "running":
      return {
        surface: "border-blue/30 bg-blue/5",
        icon: "text-blue",
        label: "text-navy",
        detail: "text-slate-600",
        badge: "border-blue/30 bg-blue/10 text-blue"
      };
    case "done":
      return {
        surface: "border-emerald-200 bg-emerald-50/70",
        icon: "text-success",
        label: "text-navy",
        detail: "text-slate-600",
        badge: "border-emerald-200 bg-emerald-50 text-success"
      };
    case "failed":
      return {
        surface: "border-red-200 bg-red-50",
        icon: "text-danger",
        label: "text-navy",
        detail: "text-danger",
        badge: "border-red-200 bg-red-50 text-danger"
      };
    default:
      return {
        surface: "border-slate-200 bg-slate-50/60",
        icon: "text-slate-400",
        label: "text-slate-700",
        detail: "text-slate-500",
        badge: "border-slate-200 bg-white text-slate-500"
      };
  }
}
