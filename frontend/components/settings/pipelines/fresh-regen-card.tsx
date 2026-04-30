"use client";

import { useState } from "react";
import { AlertTriangle } from "lucide-react";

import { FreshRegenConfirmModal } from "./fresh-regen-confirm-modal";

// Destructive entry surface on /settings/pipelines. Distinct from the
// three benign trigger cards above it: red-tinted shell, AlertTriangle
// glyph, and an explicit "DESTRUCTIVE" eyebrow so admins don't mistake
// it for one of the regular daily/weekly refreshes. Click opens a
// typed-confirmation modal that owns the chained wipe → initial_load →
// populate_all run.

interface FreshRegenCardProps {
  // Bumped after the modal sees a successful regen so the parent's
  // recent-runs table re-fetches without prop-drilling state. Mirrors
  // the existing PipelineTriggerCard onSuccess contract.
  onSuccess?: () => void;
}

export function FreshRegenCard({ onSuccess }: FreshRegenCardProps) {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="rounded-2xl border border-red-500/30 border-l-4 border-l-red-500/70 bg-red-500/[0.06] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex max-w-2xl items-start gap-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface,#ffffff)] text-[var(--pill-red-text,#b91c1c)] shadow-sm shadow-red-500/15">
            <AlertTriangle className="h-5 w-5" aria-hidden />
          </div>
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--pill-red-text,#b91c1c)]">
              Destructive · Manual only
            </p>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Fresh Regen — Destructive
            </h2>
            <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
              Wipes all broker-dealer data and re-fetches from FINRA + SEC.
              ~3,002 firms, ~1–2 hours wall-clock.{" "}
              <span className="font-semibold text-[var(--pill-red-text,#b91c1c)]">
                Cannot be undone.
              </span>
            </p>
            <p className="text-xs text-[var(--text-muted,#94a3b8)]">
              Use only when you need a clean rebuild — daily/weekly refreshes
              already run via the cards above and Cloud Scheduler.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-[0_6px_16px_rgba(220,38,38,0.35)] transition hover:bg-red-700 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none sm:w-auto"
        >
          Start Fresh Regen
        </button>
      </div>
      {confirming ? (
        <FreshRegenConfirmModal
          onClose={() => setConfirming(false)}
          onSuccess={onSuccess}
        />
      ) : null}
    </div>
  );
}
