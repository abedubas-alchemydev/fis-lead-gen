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
    <div className="rounded-[28px] border-2 border-red-300/80 bg-red-50/40 p-7 shadow-shell">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex max-w-2xl items-start gap-4">
          <div className="rounded-2xl bg-white p-3 text-danger shadow-sm shadow-red-200/60">
            <AlertTriangle className="h-5 w-5" aria-hidden />
          </div>
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-[0.22em] text-danger">
              Destructive · Manual only
            </p>
            <h2 className="text-xl font-semibold text-navy">
              Fresh Regen — Destructive
            </h2>
            <p className="text-sm leading-6 text-slate-700">
              Wipes all broker-dealer data and re-fetches from FINRA + SEC.
              ~3,002 firms, ~1–2 hours wall-clock.{" "}
              <span className="font-semibold text-danger">
                Cannot be undone.
              </span>
            </p>
            <p className="text-xs text-slate-600">
              Use only when you need a clean rebuild — daily/weekly refreshes
              already run via the cards above and Cloud Scheduler.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="inline-flex h-11 items-center rounded-2xl bg-danger px-5 text-sm font-semibold text-white shadow-lg shadow-red-300/40 transition hover:bg-[#c62a2a] hover:shadow-xl hover:shadow-red-300/50 disabled:cursor-not-allowed disabled:opacity-60"
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
