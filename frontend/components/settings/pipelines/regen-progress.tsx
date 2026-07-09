"use client";

import { CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";

// Generic per-item progress display. Originally built for the Fresh Regen
// modal's three chained phases; also reused by the favorite-list Bulk-Enrich
// modal, where each "phase" is a firm in the list. Kept a pure render concern
// so callers drive the state transitions. Status values mirror the FE state
// machine (idle/pending stay as "pending" here — callers swap them to
// "running" / "done" / "failed" / "skipped" as work progresses).
//
// "skipped" was added for Bulk-Enrich (a firm the BE gap-gated or couldn't
// enrich); Fresh Regen never emits it, so the addition is backward-compatible.

export type PhaseStatus = "pending" | "running" | "done" | "failed" | "skipped";

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
  if (status === "skipped") {
    return <MinusCircle {...props} />;
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
    case "skipped":
      return "Skipped";
    default:
      return "Pending";
  }
}

function paletteFor(status: PhaseStatus) {
  switch (status) {
    case "running":
      return {
        surface: "border-[var(--accent,#6366f1)]/30 bg-[var(--accent,#6366f1)]/5",
        icon: "text-[var(--accent,#6366f1)]",
        label: "text-[var(--text,#0f172a)]",
        detail: "text-[var(--text-dim,#475569)]",
        badge: "border-[var(--accent,#6366f1)]/30 bg-[var(--accent,#6366f1)]/10 text-[var(--accent,#6366f1)]"
      };
    case "done":
      return {
        surface: "border-emerald-200 bg-emerald-50/70",
        icon: "text-success",
        label: "text-[var(--text,#0f172a)]",
        detail: "text-[var(--text-dim,#475569)]",
        badge: "border-emerald-200 bg-emerald-50 text-success"
      };
    case "failed":
      return {
        surface: "border-red-200 bg-red-50",
        icon: "text-danger",
        label: "text-[var(--text,#0f172a)]",
        detail: "text-danger",
        badge: "border-red-200 bg-red-50 text-danger"
      };
    case "skipped":
      return {
        surface: "border-amber-200 bg-amber-50/70",
        icon: "text-amber-500",
        label: "text-[var(--text,#0f172a)]",
        detail: "text-amber-700",
        badge: "border-amber-200 bg-amber-50 text-amber-700"
      };
    default:
      return {
        surface: "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]/60",
        icon: "text-[var(--text-muted,#94a3b8)]",
        label: "text-[var(--text-dim,#475569)]",
        detail: "text-[var(--text-muted,#94a3b8)]",
        badge: "border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text-muted,#94a3b8)]"
      };
  }
}
