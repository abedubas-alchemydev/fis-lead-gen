"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
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
            className={clsx(
              buttonBase,
              buttonSizes.sm,
              "border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)]",
            )}
          >
            Cancel
          </button>
          <Button
            type="button"
            variant="danger"
            size="sm"
            onClick={handleConfirm}
            disabled={submitting}
          >
            {submitting ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>
    </div>
  );
}
