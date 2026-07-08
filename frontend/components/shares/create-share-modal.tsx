"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";

import { Button, buttonBase, buttonClass, buttonSizes } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import {
  createShare,
  describeShareCreateErrorDetail,
  resolveShareUrl,
} from "@/lib/shares-api";
import type { CreateShareResponse } from "@/types/lead-share";

import { SharePasswordReveal } from "./share-password-reveal";

const MAX_NAME_LENGTH = 80;

// Two-step "export favorite list as a public share link" modal, following
// the DeleteListDialog skeleton (backdrop blur, Esc/backdrop dismiss, inline
// error, aria-modal).
//
//   1. Confirm — name input (prefilled with the list name) + what-happens
//      copy; Cancel / "Create share link".
//   2. Result  — the ONE-TIME password reveal. Esc and backdrop dismissal
//      are deliberately disabled on this step: an accidental close loses the
//      password forever (it's hashed at rest and never retrievable). Only
//      the explicit Done button closes, and password state is wiped on the
//      way out.
export function CreateShareModal({
  listId,
  listName,
  onClose,
}: {
  listId: number;
  listName: string;
  onClose: () => void;
}) {
  const [name, setName] = useState(listName.slice(0, MAX_NAME_LENGTH));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateShareResponse | null>(null);

  const step: "confirm" | "result" = result ? "result" : "confirm";

  // Wipe the once-only password from state before unmounting — no stale
  // secret lingers if the parent keeps this subtree around.
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

  async function handleCreate() {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const trimmed = name.trim();
      const response = await createShare({
        favorite_list_id: listId,
        ...(trimmed ? { name: trimmed } : {}),
      });
      setResult(response);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? (describeShareCreateErrorDetail(err.detail) ??
            (err.detail || "Couldn't create share link."))
          : err instanceof Error
            ? err.message
            : "Couldn't create share link.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  const skipped = result?.skipped;
  const skippedParts: string[] = [];
  if (skipped) {
    if (skipped.institutional_investors > 0) {
      skippedParts.push(
        `${skipped.institutional_investors.toLocaleString()} institutional investor${skipped.institutional_investors === 1 ? "" : "s"}`,
      );
    }
    if (skipped.reporting_owners > 0) {
      skippedParts.push(
        `${skipped.reporting_owners.toLocaleString()} insider${skipped.reporting_owners === 1 ? "" : "s"}`,
      );
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-share-title"
      aria-describedby="create-share-body"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          // Backdrop dismiss only on the confirm step — see result-step
          // rationale in the component comment.
          if (step === "confirm" && !submitting) close();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.45)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[460px] rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] p-5 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.35)]">
        {step === "confirm" ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void handleCreate();
            }}
          >
            <h2
              id="create-share-title"
              className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
            >
              Create share link
            </h2>
            <p
              id="create-share-body"
              className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
            >
              Exports up to 80 broker-dealer, bank, and investment-advisor
              leads from &lsquo;{listName}&rsquo;. Institutional investors and
              insiders can&apos;t be shared and will be skipped. Recipients
              get a password-protected, read-only view.
            </p>
            <div className="mt-4">
              <Input
                label="Share name"
                value={name}
                maxLength={MAX_NAME_LENGTH}
                onChange={(event) => setName(event.target.value)}
                placeholder={listName}
                autoFocus
                className="px-3 py-2 text-[13px]"
              />
            </div>
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
              <Button type="submit" size="sm" disabled={submitting}>
                {submitting ? "Creating…" : "Create share link"}
              </Button>
            </div>
          </form>
        ) : result ? (
          <div>
            <h2
              id="create-share-title"
              className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
            >
              Share link created
            </h2>
            <p
              id="create-share-body"
              className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]"
            >
              {result.share.item_count.toLocaleString()} lead
              {result.share.item_count === 1 ? "" : "s"} included in this
              share.
            </p>
            <div className="mt-3">
              <SharePasswordReveal
                url={resolveShareUrl(result.share)}
                password={result.password}
              />
            </div>
            {skippedParts.length > 0 ? (
              <p className="mt-3 rounded-md border border-amber-500/25 bg-amber-500/12 px-3 py-2 text-[12px] leading-5 text-amber-700">
                Skipped: {skippedParts.join(", ")} — not shareable.
              </p>
            ) : null}
            {result.warnings.length > 0 ? (
              <ul className="mt-3 space-y-1 rounded-md border border-amber-500/25 bg-amber-500/12 px-3 py-2 text-[12px] leading-5 text-amber-700">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
            <div className="mt-5 flex items-center justify-between gap-3">
              <Link
                href={"/settings/shared-links" as Route}
                className="text-[12px] font-semibold text-[var(--accent,#6366f1)] transition hover:underline"
              >
                Manage shared links →
              </Link>
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
