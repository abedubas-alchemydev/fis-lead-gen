"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

export type ShareActionMode = "revoke" | "unrevoke" | "delete";

// Copy variants per action. Revoke/delete are destructive (danger CTA);
// unrevoke restores access, so it gets the primary CTA.
const MODE_COPY: Record<
  ShareActionMode,
  { titleVerb: string; body: string; cta: string; ctaBusy: string; danger: boolean }
> = {
  revoke: {
    titleVerb: "Revoke",
    body: "Recipients with the link will immediately lose access. You can unrevoke it later to restore access at the same URL.",
    cta: "Revoke",
    ctaBusy: "Revoking…",
    danger: true,
  },
  unrevoke: {
    titleVerb: "Unrevoke",
    body: "The link becomes accessible again at the same URL, with the same password.",
    cta: "Unrevoke",
    ctaBusy: "Unrevoking…",
    danger: false,
  },
  delete: {
    titleVerb: "Delete",
    body: "The link stops working immediately and its view history is permanently removed. This can't be undone.",
    cta: "Delete",
    ctaBusy: "Deleting…",
    danger: true,
  },
};

// Confirm dialog for share-link state changes — a DeleteListDialog clone
// with mode-driven copy. Focuses Cancel on mount (safer default), Esc and
// backdrop dismiss, and surfaces ApiError.detail inline (e.g. the 409
// wrong-state responses) so the user knows why the action didn't go
// through. On success the parent closes the dialog from onConfirm's
// resolution.
export function ConfirmShareActionDialog({
  mode,
  shareName,
  onCancel,
  onConfirm,
}: {
  mode: ShareActionMode;
  shareName: string;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  const copy = MODE_COPY[mode];

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
      setError(message || `Couldn't ${mode} share link.`);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="share-action-title"
      aria-describedby="share-action-body"
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
          id="share-action-title"
          className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
        >
          {copy.titleVerb} &lsquo;{shareName}&rsquo;?
        </h2>
        <p
          id="share-action-body"
          className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
        >
          {copy.body}
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
            variant={copy.danger ? "danger" : "primary"}
            size="sm"
            onClick={() => void handleConfirm()}
            disabled={submitting}
          >
            {submitting ? copy.ctaBusy : copy.cta}
          </Button>
        </div>
      </div>
    </div>
  );
}
