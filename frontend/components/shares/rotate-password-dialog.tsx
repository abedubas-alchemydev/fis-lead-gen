"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";

import { Button, buttonBase, buttonClass, buttonSizes } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { resolveShareUrl } from "@/lib/shares-api";
import type { RotatePasswordResponse, ShareSummary } from "@/types/lead-share";

import { SharePasswordReveal } from "./share-password-reveal";

// Two-step rotate-password dialog for a share link, following the
// DeleteListDialog skeleton. Confirm warns that the current password dies
// immediately; the result step reuses SharePasswordReveal under the same
// once-only rules as the create modal (no Esc/backdrop dismissal — an
// accidental close loses the new password forever; state wiped on close).
//
// The parent supplies onRotate so it can update its own row state
// (password_rotated_at) from the response before this dialog renders it.
// A 409 (share revoked) surfaces inline via ApiError.detail.
export function RotatePasswordDialog({
  share,
  onRotate,
  onClose,
}: {
  share: ShareSummary;
  onRotate: () => Promise<RotatePasswordResponse>;
  onClose: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RotatePasswordResponse | null>(null);

  const step: "confirm" | "result" = result ? "result" : "confirm";

  const close = useCallback(() => {
    setResult(null);
    onClose();
  }, [onClose]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      // Result step shows the once-only password — Esc must not close it.
      if (event.key === "Escape" && step === "confirm" && !submitting) {
        close();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [step, submitting, close]);

  async function handleRotate() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await onRotate();
      setResult(response);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : null;
      setError(message || "Couldn't rotate password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="rotate-password-title"
      aria-describedby="rotate-password-body"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (step === "confirm" && !submitting) close();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.45)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[460px] rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] p-5 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        {step === "confirm" ? (
          <>
            <h2
              id="rotate-password-title"
              className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
            >
              Rotate password for &lsquo;{share.name}&rsquo;?
            </h2>
            <p
              id="rotate-password-body"
              className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
            >
              The current password stops working immediately for everyone.
              You&apos;ll get a new password to send to recipients — the link
              itself stays the same.
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
                type="button"
                onClick={close}
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
                size="sm"
                onClick={() => void handleRotate()}
                disabled={submitting}
              >
                {submitting ? "Rotating…" : "Rotate password"}
              </Button>
            </div>
          </>
        ) : result ? (
          <div>
            <h2
              id="rotate-password-title"
              className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
            >
              Password rotated
            </h2>
            <p
              id="rotate-password-body"
              className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
            >
              The old password no longer works. Share the new one with your
              recipients.
            </p>
            <div className="mt-3">
              <SharePasswordReveal
                url={resolveShareUrl(share)}
                password={result.password}
              />
            </div>
            <div className="mt-5 flex items-center justify-end">
              {/* Raw <button> so we can autoFocus (the shared Button doesn't
                  forward refs); classes come from the same buttonClass scale. */}
              <button
                type="button"
                autoFocus
                onClick={close}
                className={buttonClass({ size: "sm" })}
              >
                Done
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
