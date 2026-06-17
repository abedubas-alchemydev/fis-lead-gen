"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import {
  AlertCircle,
  Clock,
  FileText,
  Loader2,
  Mail,
  Pencil,
  Sparkles,
  Trash2,
} from "lucide-react";

import {
  ApiError,
  deleteOutreachDraft,
  getOutreachDraft,
  listOutreachDrafts,
} from "@/lib/api";
import type {
  SavedOutreachDraft,
  SavedOutreachDraftDetail,
  SavedOutreachDraftsListResponse,
} from "@/lib/types";
import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";
const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";
const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const PAGE_SIZE = 50;

// First To recipient's label + how many additional recipients (extra To +
// Cc + Bcc) the draft carries, for the compact list cell.
function primaryRecipient(draft: SavedOutreachDraft): string {
  const first = draft.to[0];
  if (first) return first.name || first.email;
  return "";
}

function extraRecipientCount(draft: SavedOutreachDraft): number {
  return Math.max(0, draft.to.length - 1) + draft.cc.length + draft.bcc.length;
}

export function DraftsTab({
  onEditDraft,
}: {
  onEditDraft: (draft: SavedOutreachDraftDetail) => void;
}) {
  const toast = useToast();
  const [data, setData] = useState<SavedOutreachDraftsListResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [openingId, setOpeningId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SavedOutreachDraft | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await listOutreachDrafts({ limit: PAGE_SIZE, offset }));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load drafts.",
      );
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // Edit loads the full draft (the list row omits the body) then hands it up
  // to the workspace, which switches to the Create tab pre-filled.
  async function handleEdit(draft: SavedOutreachDraft) {
    setOpeningId(draft.id);
    try {
      const detail = await getOutreachDraft(draft.id);
      onEditDraft(detail);
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.detail || err.message
          : "Couldn't open this draft.",
      );
    } finally {
      setOpeningId(null);
    }
  }

  const confirmDelete = useCallback(
    async (draft: SavedOutreachDraft) => {
      // Throws on failure -> the dialog surfaces the reason and stays open.
      await deleteOutreachDraft(draft.id);
      const wasLastOnPage = (data?.items.length ?? 0) <= 1;
      setData((current) =>
        current
          ? {
              ...current,
              items: current.items.filter((d) => d.id !== draft.id),
              total: Math.max(0, current.total - 1),
            }
          : current,
      );
      if (wasLastOnPage && offset > 0) {
        setOffset((current) => Math.max(0, current - PAGE_SIZE));
      }
      toast.success("Draft deleted.");
      setPendingDelete(null);
    },
    [data, offset, toast],
  );

  const total = data?.total ?? 0;
  const pageCount = data?.items.length ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageMax = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6">
      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Your drafts</p>
            <h2 className={CARD_TITLE}>
              {loading && !data
                ? "Loading..."
                : total === 0
                  ? "No saved drafts"
                  : `${total} ${total === 1 ? "draft" : "drafts"}`}
            </h2>
          </div>
        </div>

        {loading && !data ? (
          <div className="mt-6 flex items-center gap-2 text-sm text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            <span>Loading your drafts...</span>
          </div>
        ) : null}

        {!loading && total === 0 ? <EmptyState /> : null}

        {data && total > 0 ? (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="px-5 py-3">Updated</th>
                  <th className="px-5 py-3">Recipient</th>
                  <th className="px-5 py-3">Service</th>
                  <th className="px-5 py-3">Subject</th>
                  <th className="px-5 py-3">Source</th>
                  <th className="px-5 py-3 text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {data.items.map((draft) => {
                  const updatedAt = new Date(draft.updated_at);
                  const recipient = primaryRecipient(draft);
                  const extra = extraRecipientCount(draft);
                  return (
                    <tr
                      key={draft.id}
                      className="cursor-pointer hover:bg-[var(--surface-2,#f1f6fd)]/50"
                      onClick={() => void handleEdit(draft)}
                    >
                      <td className="px-5 py-4 text-[var(--text-muted,#94a3b8)]">
                        <span
                          className="inline-flex items-center gap-1.5"
                          title={updatedAt.toLocaleString()}
                        >
                          <Clock className="h-3.5 w-3.5" aria-hidden />
                          {updatedAt.toLocaleDateString()}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-[var(--text,#0f172a)]">
                        {recipient ? (
                          <span className="inline-flex items-center gap-1.5">
                            <Mail
                              className="h-3 w-3 text-[var(--text-muted,#94a3b8)]"
                              aria-hidden
                            />
                            <span className="font-medium">{recipient}</span>
                            {extra > 0 ? (
                              <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                                +{extra} more
                              </span>
                            ) : null}
                          </span>
                        ) : (
                          <span className="text-[var(--text-muted,#94a3b8)]">
                            No recipients yet
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
                        {draft.folder_name ?? (
                          <span className="text-[var(--text-muted,#94a3b8)]">
                            —
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-[var(--text,#0f172a)]">
                        <div
                          className="max-w-[280px] truncate"
                          title={draft.subject || undefined}
                        >
                          {draft.subject || (
                            <span className="italic text-[var(--text-muted,#94a3b8)]">
                              (no subject)
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <SourcePill source={draft.source} />
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleEdit(draft);
                            }}
                            aria-label="Edit this draft"
                            title="Edit"
                            className="inline-flex items-center justify-center rounded-md p-1.5 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--accent,#6366f1)]"
                          >
                            {openingId === draft.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                            ) : (
                              <Pencil className="h-4 w-4" aria-hidden />
                            )}
                          </button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setPendingDelete(draft);
                            }}
                            aria-label="Delete this draft"
                            title="Delete"
                            className="inline-flex items-center justify-center rounded-md p-1.5 text-[var(--text-muted,#94a3b8)] transition hover:bg-red-500/10 hover:text-[var(--pill-red-text,#b91c1c)]"
                          >
                            <Trash2 className="h-4 w-4" aria-hidden />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {data && total > 0 ? (
          <div className="mt-4 flex items-center justify-between gap-3 text-xs text-[var(--text-dim,#475569)]">
            <span>
              Showing {offset + 1}–{offset + pageCount} of {total}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Previous
              </button>
              <span>
                Page {page} of {pageMax}
              </span>
              <button
                type="button"
                disabled={offset + PAGE_SIZE >= total || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {pendingDelete ? (
        <DeleteDraftDialog
          draft={pendingDelete}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => confirmDelete(pendingDelete)}
        />
      ) : null}
    </div>
  );
}

function SourcePill({ source }: { source: string }) {
  if (source === "doxie") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.1)] px-2.5 py-0.5 text-[11px] font-semibold text-[#4338ca]">
        <Sparkles className="h-3 w-3" strokeWidth={2.5} aria-hidden />
        Doxie
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--text-dim,#475569)]">
      <FileText className="h-3 w-3" strokeWidth={2.5} aria-hidden />
      Manual
    </span>
  );
}

function EmptyState() {
  return (
    <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
      <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
        <FileText className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
        No saved drafts yet
      </p>
      <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
        Compose a message on the{" "}
        <Link
          href={"/outreach/sent?tab=create" as Route}
          className="font-semibold text-[var(--text,#0f172a)] underline-offset-4 hover:underline"
        >
          Create outreach
        </Link>{" "}
        tab and click <span className="font-semibold">Save draft</span> to keep
        it here.
      </p>
    </div>
  );
}

// Compact confirm dialog for hard-deleting a draft. Mirrors DeleteSendDialog
// but the copy makes clear a draft delete is permanent (no audit kept).
function DeleteDraftDialog({
  draft,
  onCancel,
  onConfirm,
}: {
  draft: SavedOutreachDraft;
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
      setError(message || "Couldn't delete this draft.");
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-draft-title"
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
          id="delete-draft-title"
          className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]"
        >
          Delete this draft?
        </h2>
        <p className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          &ldquo;{draft.subject || "(no subject)"}&rdquo; will be permanently
          deleted.
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
