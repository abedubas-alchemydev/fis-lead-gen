"use client";

import { useEffect, useRef, useState } from "react";

// Centered confirm dialog for kicking off a long-running pipeline run. We
// build it locally instead of reusing my-favorites/delete-list-dialog
// (forbidden path) and instead of adding to components/ui/ (also forbidden
// per cli02 brief). Same a11y idiom: backdrop click + Esc dismiss, focus
// the safe option (Cancel) on mount, busy guard while the POST is in
// flight so the user can't double-trigger by hammering Enter.

interface ConfirmTriggerDialogProps {
  pipelineName: string;
  eta: string;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}

export function ConfirmTriggerDialog({
  pipelineName,
  eta,
  onCancel,
  onConfirm,
}: ConfirmTriggerDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) {
        onCancel();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel, submitting]);

  async function handleConfirm() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onConfirm();
    } finally {
      // Parent owns success/error feedback (toast). Always reset so a
      // second confirm after a transient failure is possible without
      // remounting.
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-trigger-title"
      aria-describedby="confirm-trigger-body"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (!submitting) onCancel();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.45)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[440px] rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        <h2
          id="confirm-trigger-title"
          className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
        >
          Trigger {pipelineName}?
        </h2>
        <p
          id="confirm-trigger-body"
          className="mt-3 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
        >
          This kicks off an async run on the backend. Expected duration:{" "}
          <span className="font-medium text-[var(--text,#0f172a)]">{eta}</span>.
          Recent runs will show the new entry once you reload.
        </p>
        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-4 py-2 text-sm font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-4 py-2 text-sm font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none"
          >
            {submitting ? "Starting…" : "Run now"}
          </button>
        </div>
      </div>
    </div>
  );
}
