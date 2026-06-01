"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  Mail,
  MailX,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  ApiError,
  deleteOutreachSend,
  getOutreachSend,
  listOutreachSends,
} from "@/lib/api";
import type {
  OutreachSendDetail,
  OutreachSendItem,
  OutreachSendStatus,
  OutreachSendsListResponse,
  OutreachSendsScope,
} from "@/lib/types";
import { Segmented, type SegmentedItem } from "@/components/ui/segmented";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const PAGE_SIZE = 50;

type StatusFilter = "all" | OutreachSendStatus;

const STATUS_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "all", label: "All" },
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
];

const SCOPE_ITEMS: ReadonlyArray<SegmentedItem> = [
  { value: "mine", label: "My sends" },
  { value: "all", label: "All users" },
];

export function SentHistoryTab({ isAdmin = false }: { isAdmin?: boolean }) {
  const [data, setData] = useState<OutreachSendsListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  // Default to "mine" even for admins so the page doesn't change shape
  // on them without an explicit click. Non-admins never see the toggle.
  const [scope, setScope] = useState<OutreachSendsScope>("mine");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [detailCache, setDetailCache] = useState<
    Record<number, OutreachSendDetail>
  >({});
  const [detailLoading, setDetailLoading] = useState<number | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  // Per-row delete: which row is showing the inline confirm, which row's
  // delete is in flight, and the last delete failure.
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    // A reload (filter / scope / page change, or a post-delete refetch)
    // drops any lingering confirm prompt or stale delete error.
    setConfirmingId(null);
    setActionError(null);
    try {
      const result = await listOutreachSends({
        limit: PAGE_SIZE,
        offset,
        status: statusFilter === "all" ? undefined : statusFilter,
        scope: isAdmin && scope === "all" ? "all" : undefined,
      });
      setData(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load sent outreach."
      );
    } finally {
      setLoading(false);
    }
  }, [isAdmin, offset, scope, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleExpand(item: OutreachSendItem) {
    if (expandedId === item.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(item.id);
    setDetailError(null);
    if (detailCache[item.id]) return;
    setDetailLoading(item.id);
    try {
      const detail = await getOutreachSend(
        item.id,
        isAdmin && scope === "all" ? "all" : undefined
      );
      setDetailCache((prev) => ({ ...prev, [item.id]: detail }));
    } catch (err) {
      setDetailError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load message body."
      );
    } finally {
      setDetailLoading(null);
    }
  }

  async function handleDelete(id: number) {
    setDeletingId(id);
    setActionError(null);
    try {
      await deleteOutreachSend(id);
      setConfirmingId(null);
      // Collapse + drop the cached body if the deleted row was open.
      setExpandedId((cur) => (cur === id ? null : cur));
      setDetailCache((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
      // Refetch so total + pagination stay accurate.
      await load();
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to delete this message."
      );
    } finally {
      setDeletingId(null);
    }
  }

  const total = data?.total ?? 0;
  const pageCount = data?.items.length ?? 0;
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pageMax = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const showSenderColumn = isAdmin && scope === "all";
  // Delete is owner-only. The admin "all users" scope is read-only, so the
  // Actions column / button only appear when every visible row is the
  // caller's own (i.e. not the all-users view).
  const canDelete = !showSenderColumn;

  return (
    <div className="space-y-6">
      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {actionError ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{actionError}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>
              {showSenderColumn ? "All users' sends" : "Your sends"}
            </p>
            <h2 className={CARD_TITLE}>
              {loading && !data
                ? "Loading..."
                : total === 0
                  ? "Nothing sent yet"
                  : `${total} ${total === 1 ? "message" : "messages"}`}
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <Segmented
                value={scope}
                onChange={(next) => {
                  setScope(next as OutreachSendsScope);
                  setOffset(0);
                  setExpandedId(null);
                  // Detail cache is keyed by id and can collide between
                  // scopes (an admin's own send appears in both views),
                  // so drop it to avoid serving a "mine" payload that
                  // lacks sender fields when scope=all is active.
                  setDetailCache({});
                }}
                items={SCOPE_ITEMS}
                ariaLabel="Scope"
              />
            ) : null}
            <Segmented
              value={statusFilter}
              onChange={(next) => {
                setStatusFilter(next as StatusFilter);
                setOffset(0);
                setExpandedId(null);
              }}
              items={STATUS_ITEMS}
              ariaLabel="Status"
            />
          </div>
        </div>

        {loading && !data ? (
          <div className="mt-6 flex items-center gap-2 text-sm text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            <span>Loading your sent outreach...</span>
          </div>
        ) : null}

        {!loading && total === 0 ? (
          <EmptyState filter={statusFilter} scope={scope} />
        ) : null}

        {data && total > 0 ? (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="px-5 py-3">Sent</th>
                  {showSenderColumn ? <th className="px-5 py-3">Sender</th> : null}
                  <th className="px-5 py-3">Recipient</th>
                  <th className="px-5 py-3">Firm</th>
                  <th className="px-5 py-3">Service</th>
                  <th className="px-5 py-3">Subject</th>
                  <th className="px-5 py-3">Status</th>
                  {canDelete ? (
                    <th className="px-5 py-3 text-right">Actions</th>
                  ) : null}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {data.items.map((item) => {
                  const isExpanded = expandedId === item.id;
                  return (
                    <RowGroup
                      key={item.id}
                      item={item}
                      isExpanded={isExpanded}
                      detail={detailCache[item.id] ?? null}
                      detailLoading={detailLoading === item.id}
                      detailError={isExpanded ? detailError : null}
                      onToggle={() => void handleExpand(item)}
                      showSender={showSenderColumn}
                      showActions={canDelete}
                      confirming={confirmingId === item.id}
                      deleting={deletingId === item.id}
                      onRequestDelete={() => {
                        setConfirmingId(item.id);
                        setActionError(null);
                      }}
                      onConfirmDelete={() => void handleDelete(item.id)}
                      onCancelDelete={() => setConfirmingId(null)}
                    />
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
                disabled={!canPrev || loading}
                onClick={() => {
                  setExpandedId(null);
                  setOffset(Math.max(0, offset - PAGE_SIZE));
                }}
                className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Previous
              </button>
              <span>
                Page {page} of {pageMax}
              </span>
              <button
                type="button"
                disabled={!canNext || loading}
                onClick={() => {
                  setExpandedId(null);
                  setOffset(offset + PAGE_SIZE);
                }}
                className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RowGroup({
  item,
  isExpanded,
  detail,
  detailLoading,
  detailError,
  onToggle,
  showSender,
  showActions,
  confirming,
  deleting,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: {
  item: OutreachSendItem;
  isExpanded: boolean;
  detail: OutreachSendDetail | null;
  detailLoading: boolean;
  detailError: string | null;
  onToggle: () => void;
  showSender: boolean;
  showActions: boolean;
  confirming: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}) {
  const sentAt = useMemo(() => new Date(item.sent_at), [item.sent_at]);
  return (
    <>
      <tr
        className="cursor-pointer hover:bg-[var(--surface-2,#f1f6fd)]/50"
        onClick={onToggle}
        aria-expanded={isExpanded}
      >
        <td className="px-5 py-4 text-[var(--text-muted,#94a3b8)]">
          <span
            className="inline-flex items-center gap-1.5"
            title={sentAt.toLocaleString()}
          >
            <Clock className="h-3.5 w-3.5" aria-hidden />
            {sentAt.toLocaleDateString()}
          </span>
        </td>
        {showSender ? (
          <td className="px-5 py-4 text-[var(--text,#0f172a)]">
            <div className="font-semibold">{item.sender_name || "—"}</div>
            {item.sender_email ? (
              <div className="mt-0.5 inline-flex items-center gap-1.5 text-xs text-[var(--text-dim,#475569)]">
                <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" aria-hidden />
                {item.sender_email}
              </div>
            ) : null}
          </td>
        ) : null}
        <td className="px-5 py-4 text-[var(--text,#0f172a)]">
          <div className="font-semibold">
            {item.contact_name || item.recipient_name || (
              <span className="text-[var(--text-muted,#94a3b8)]">
                {item.firm_type === "adhoc" ? "One-off recipient" : "—"}
              </span>
            )}
          </div>
          {item.contact_email || item.recipient_email ? (
            <div className="mt-0.5 inline-flex items-center gap-1.5 text-xs text-[var(--text-dim,#475569)]">
              <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" aria-hidden />
              {item.contact_email ?? item.recipient_email}
            </div>
          ) : null}
        </td>
        <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
          {item.broker_dealer_id ? (
            <Link
              href={`/master-list/${item.broker_dealer_id}`}
              onClick={(e) => e.stopPropagation()}
              className="underline-offset-4 hover:underline"
            >
              {item.broker_dealer_name || "—"}
            </Link>
          ) : (
            <span className="text-[var(--text-muted,#94a3b8)]">
              {item.advisor_name ?? item.institutional_investor_name ?? "—"}
            </span>
          )}
        </td>
        <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
          {item.folder_name ?? <span className="text-[var(--text-muted,#94a3b8)]">(deleted)</span>}
        </td>
        <td className="px-5 py-4 text-[var(--text,#0f172a)]">
          <div className="max-w-[280px] truncate" title={item.subject}>
            {item.subject}
          </div>
        </td>
        <td className="px-5 py-4">
          <StatusPill status={item.status} error={item.error} />
        </td>
        {showActions ? (
          <td
            className="px-5 py-4 text-right"
            onClick={(e) => e.stopPropagation()}
          >
            <DeleteCell
              confirming={confirming}
              deleting={deleting}
              onRequestDelete={onRequestDelete}
              onConfirmDelete={onConfirmDelete}
              onCancelDelete={onCancelDelete}
            />
          </td>
        ) : null}
      </tr>
      {isExpanded ? (
        <tr className="bg-[var(--surface-2,#f1f6fd)]/40">
          <td
            colSpan={6 + (showSender ? 1 : 0) + (showActions ? 1 : 0)}
            className="px-5 py-5"
          >
            <ExpandedBody
              item={item}
              detail={detail}
              loading={detailLoading}
              error={detailError}
            />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ExpandedBody({
  item,
  detail,
  loading,
  error,
}: {
  item: OutreachSendItem;
  detail: OutreachSendDetail | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--text-dim,#475569)]">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        <span>Loading message body...</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>{error}</span>
      </div>
    );
  }
  if (!detail) {
    return null;
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className={EYEBROW}>Message</p>
        {item.status === "sent" && item.gmail_message_id ? (
          <a
            href="https://mail.google.com/mail/u/0/#sent"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-semibold text-[var(--text-dim,#475569)] underline-offset-4 hover:underline"
          >
            Open in Gmail Sent
          </a>
        ) : null}
      </div>
      <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-4">
        <div className="text-xs font-semibold text-[var(--text-muted,#94a3b8)]">
          Subject
        </div>
        <div className="mt-1 text-sm text-[var(--text,#0f172a)]">
          {detail.subject}
        </div>
        <div className="mt-4 text-xs font-semibold text-[var(--text-muted,#94a3b8)]">
          Body
        </div>
        <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm leading-6 text-[var(--text,#0f172a)]">
          {detail.body}
        </pre>
      </div>
      {item.status === "failed" && item.error ? (
        <div className="rounded-xl border border-red-500/25 bg-red-500/8 px-4 py-3 text-xs text-[var(--pill-red-text,#b91c1c)]">
          <span className="font-semibold">Failure reason: </span>
          <code className="font-mono">{item.error}</code>
        </div>
      ) : null}
    </div>
  );
}

function DeleteCell({
  confirming,
  deleting,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
}: {
  confirming: boolean;
  deleting: boolean;
  onRequestDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}) {
  if (deleting) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[var(--text-dim,#475569)]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        Deleting...
      </span>
    );
  }
  if (confirming) {
    return (
      <span className="inline-flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={onConfirmDelete}
          className="rounded-[7px] border border-red-500/30 bg-red-500/12 px-2.5 py-1 text-[11px] font-semibold text-[var(--pill-red-text,#b91c1c)] transition hover:bg-red-500/20"
        >
          Delete
        </button>
        <button
          type="button"
          onClick={onCancelDelete}
          className="rounded-[7px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onRequestDelete}
      aria-label="Delete message"
      title="Delete"
      className="inline-grid h-8 w-8 place-items-center rounded-[8px] border border-transparent text-[var(--text-muted,#94a3b8)] transition hover:border-red-500/25 hover:bg-red-500/10 hover:text-[var(--pill-red-text,#b91c1c)]"
    >
      <Trash2 className="h-4 w-4" aria-hidden />
    </button>
  );
}

function StatusPill({
  status,
  error,
}: {
  status: string;
  error: string | null;
}) {
  if (status === "sent") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/25 bg-emerald-500/12 px-2.5 py-0.5 text-[11px] font-semibold text-[var(--pill-green-text,#047857)]">
        <CheckCircle2 className="h-3 w-3" strokeWidth={2.5} aria-hidden />
        Sent
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-red-500/25 bg-red-500/12 px-2.5 py-0.5 text-[11px] font-semibold text-[var(--pill-red-text,#b91c1c)]"
      title={error ?? undefined}
    >
      <XCircle className="h-3 w-3" strokeWidth={2.5} aria-hidden />
      Failed
    </span>
  );
}

function EmptyState({
  filter,
  scope,
}: {
  filter: StatusFilter;
  scope: OutreachSendsScope;
}) {
  if (filter === "failed") {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
        <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
          <MailX className="h-6 w-6" strokeWidth={1.75} aria-hidden />
        </div>
        <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
          No failed sends — nice
        </p>
        <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
          Failed attempts (OAuth issues, Gmail rejections) would show up here.
        </p>
      </div>
    );
  }
  if (scope === "all") {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
        <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
          <Send className="h-6 w-6" strokeWidth={1.75} aria-hidden />
        </div>
        <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
          No outreach has been sent yet
        </p>
        <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
          No user has sent an outreach email through the platform.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
      <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
        <Send className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
        You haven&apos;t sent any outreach yet
      </p>
      <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
        Switch to the{" "}
        <Link
          href={"/outreach/sent?tab=create" as Route}
          className="font-semibold text-[var(--text,#0f172a)] underline-offset-4 hover:underline"
        >
          Create outreach
        </Link>{" "}
        tab or open a firm on the{" "}
        <Link
          href="/master-list"
          className="font-semibold text-[var(--text,#0f172a)] underline-offset-4 hover:underline"
        >
          Master List
        </Link>{" "}
        and use a contact&apos;s Outreach button to draft your first email.
      </p>
    </div>
  );
}
