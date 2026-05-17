"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Bookmark,
  Briefcase,
  Clock,
  Loader2,
  ShieldCheck,
  Star,
} from "lucide-react";

import { ApiError, getUserSavedFirms } from "@/lib/api";
import type {
  AdminSavedFirmRow,
  AdminUserSavedFirmsResponse,
} from "@/lib/types";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const PAGE_SIZE = 50;

type ListFilter = "all" | number;

function firmHref(row: AdminSavedFirmRow): Route {
  return row.item_type === "broker_dealer"
    ? (`/master-list/${row.target_id}` as Route)
    : (`/advisor-list/${row.target_id}` as Route);
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function UserSavedFirmsClient({
  user,
}: {
  user: { id: string; email: string; name: string };
}) {
  const [data, setData] = useState<AdminUserSavedFirmsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listFilter, setListFilter] = useState<ListFilter>("all");
  const [offset, setOffset] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getUserSavedFirms(user.id, {
        limit: PAGE_SIZE,
        offset,
        listId: listFilter === "all" ? undefined : listFilter,
      });
      setData(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load saved firms."
      );
    } finally {
      setLoading(false);
    }
  }, [user.id, offset, listFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = data?.total ?? 0;
  const lists = data?.lists ?? [];
  const items = data?.items ?? [];
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  function selectListFilter(next: ListFilter) {
    setListFilter(next);
    setOffset(0);
  }

  const displayName = user.name?.trim() || user.email;

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link href="/settings/users" className="hover:underline">Users</Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={`/settings/users/${user.id}` as Route}
              className="hover:underline"
            >
              {displayName}
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Saved firms
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Saved firms for {displayName}
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            {lists.length} {lists.length === 1 ? "list" : "lists"}
            {" · "}
            {total} {total === 1 ? "firm saved" : "firms saved"}
          </p>
        </div>
        <Link
          href={`/settings/users/${user.id}` as Route}
          className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back to user
        </Link>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Saved firms</p>
            <h2 className={CARD_TITLE}>
              {total === 0
                ? "Nothing saved yet"
                : `${total} ${total === 1 ? "firm" : "firms"} across all lists`}
            </h2>
          </div>
          {lists.length > 0 ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-[var(--text,#0f172a)]">
              <Bookmark className="h-3 w-3" strokeWidth={2.5} aria-hidden />
              {lists.length} {lists.length === 1 ? "list" : "lists"}
            </span>
          ) : null}
        </div>

        {lists.length > 0 ? (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => selectListFilter("all")}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${
                listFilter === "all"
                  ? "border-[var(--accent,#1e40af)] bg-[var(--accent,#1e40af)] text-white"
                  : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)] hover:bg-[var(--surface-2,#f1f6fd)]"
              }`}
            >
              All ({total})
            </button>
            {lists.map((list) => {
              const active = listFilter === list.id;
              return (
                <button
                  key={list.id}
                  type="button"
                  onClick={() => selectListFilter(list.id)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition ${
                    active
                      ? "border-[var(--accent,#1e40af)] bg-[var(--accent,#1e40af)] text-white"
                      : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)] hover:bg-[var(--surface-2,#f1f6fd)]"
                  }`}
                >
                  {list.is_default ? (
                    <Star className="h-3 w-3" strokeWidth={2.5} aria-hidden />
                  ) : null}
                  {list.name} ({list.item_count})
                </button>
              );
            })}
          </div>
        ) : null}

        {loading ? (
          <div className="mt-6 flex items-center justify-center gap-2 py-12 text-sm text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading saved firms…
          </div>
        ) : items.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-12 text-center">
            <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
              {listFilter === "all"
                ? "This user hasn't saved any firms yet"
                : "No firms in this list"}
            </p>
            <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
              {listFilter === "all"
                ? "When they bookmark a broker-dealer or investment advisor, it will show up here."
                : "Pick another list above to see its contents."}
            </p>
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="px-5 py-3">Firm</th>
                  <th className="px-5 py-3">Type</th>
                  <th className="px-5 py-3">List</th>
                  <th className="px-5 py-3">Saved at</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {items.map((row) => (
                  <tr
                    key={`${row.item_type}-${row.target_id}-${row.list_id}`}
                    className="hover:bg-[var(--surface-2,#f1f6fd)]/50"
                  >
                    <td className="px-5 py-4 font-semibold text-[var(--text,#0f172a)]">
                      <Link
                        href={firmHref(row)}
                        className="hover:underline"
                      >
                        {row.target_name}
                      </Link>
                    </td>
                    <td className="px-5 py-4">
                      {row.item_type === "broker_dealer" ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--text,#0f172a)]">
                          <Briefcase className="h-3 w-3" aria-hidden />
                          Broker-dealer
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/25 bg-emerald-500/12 px-2.5 py-0.5 text-[11px] font-semibold text-[var(--pill-green-text,#047857)]">
                          <ShieldCheck className="h-3 w-3" aria-hidden />
                          Advisor
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
                      <span className="inline-flex items-center gap-1.5">
                        {row.list_is_default ? (
                          <Star
                            className="h-3 w-3 text-amber-500"
                            strokeWidth={2.5}
                            aria-hidden
                          />
                        ) : null}
                        {row.list_name}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-[var(--text-muted,#94a3b8)]">
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5" aria-hidden />
                        {formatDateTime(row.saved_at)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {total > PAGE_SIZE ? (
          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-xs text-[var(--text-muted,#94a3b8)]">
              Showing {Math.min(offset + 1, total)}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </p>
            <div className="inline-flex gap-2">
              <button
                type="button"
                disabled={!canPrev || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Prev
              </button>
              <button
                type="button"
                disabled={!canNext || loading}
                onClick={() => setOffset(offset + PAGE_SIZE)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
