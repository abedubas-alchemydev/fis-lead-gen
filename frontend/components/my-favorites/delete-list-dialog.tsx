"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

// Confirm dialog for destructive list deletion. Renders centered card with
// backdrop, focuses the cancel button on mount (safer default), Esc and
// backdrop click dismiss, and surfaces server errors inline so the user
// knows why the delete didn't go through. Phase-2 (#17) only.
export function DeleteListDialog({
  listName,
  itemCount,
  onCancel,
  onConfirm,
}: {
  listName: string;
  itemCount: number;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !submitting) onCancel();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel, submitting]);

  async function handleConfirm() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setSubmitting(false);
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : null;
      setError(message || "Couldn't delete list.");
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-list-title"
      aria-describedby="delete-list-body"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (!submitting) onCancel();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.45)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[420px] rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] p-5 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        <h2
          id="delete-list-title"
          className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
        >
          Delete &lsquo;{listName}&rsquo;?
        </h2>
        <p
          id="delete-list-body"
          className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
        >
          {itemCount === 0
            ? "This list is empty. This can't be undone."
            : `The ${itemCount.toLocaleString()} firm${itemCount === 1 ? "" : "s"} in this list will also be removed. This can't be undone.`}
        </p>
        {error ? (
          <p
            role="alert"
            className="mt-3 rounded-md border border-[rgba(220,38,38,0.3)] bg-[rgba(220,38,38,0.06)] px-3 py-2 text-[12px] leading-5 text-[var(--red,#dc2626)]"
          >
            {error}
          </p>
        ) : null}
        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="inline-flex h-8 items-center rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className="inline-flex h-8 items-center rounded-md border border-[rgba(220,38,38,0.5)] bg-[var(--red,#dc2626)] px-3 text-[12px] font-semibold text-white transition hover:bg-[#b91c1c] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
