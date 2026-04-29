"use client";

import Link from "next/link";
import type { Route } from "next";
import { ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { getFavoriteListItems } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import {
  MASTER_LIST_STATE_DEFAULTS,
  encodeReturnParam,
} from "@/lib/master-list-state";
import type { FavoriteList, FavoriteListItem } from "@/types/favorite-list";

import { EmptyItemsState } from "./empty-items-state";

// Right pane on /my-favorites. Fetches the active list's items and
// renders a paginated list with broker-dealer links into the firm-
// detail page. Page state lives in the URL via `?page=` so back-nav
// and reload preserve position.
//
// Read-only this PR — no per-row remove or move-between-lists. Phase 2
// will add those once the BE PUT/DELETE endpoints land.
export function FavoriteListItemsPane({
  activeList,
  page,
  pageSize,
  onPageChange,
}: {
  activeList: FavoriteList;
  page: number;
  pageSize: number;
  onPageChange: (next: number) => void;
}) {
  const [items, setItems] = useState<FavoriteListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    getFavoriteListItems(activeList.id, page, pageSize)
      .then((response) => {
        if (!active) return;
        setItems(response.items);
        setTotal(response.total);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error ? err.message : "Couldn't load favorites",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [activeList.id, page, pageSize, reloadKey]);

  // Source param so the firm-detail Next-Lead button walks favorites,
  // matching the existing single-list behavior pre-#17.
  const detailHrefSuffix = useMemo(() => {
    const env = encodeReturnParam({
      ...MASTER_LIST_STATE_DEFAULTS,
      source: "favorites",
    });
    return env ? `?return=${env}` : "";
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);

  return (
    <div>
      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-[16px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            {activeList.name}
          </h2>
          <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {total.toLocaleString()} firm{total === 1 ? "" : "s"} in this list
          </p>
        </div>
      </header>

      {loading ? (
        <ItemsSkeleton />
      ) : error ? (
        <ErrorState
          message={error}
          onRetry={() => {
            setReloadKey((k) => k + 1);
          }}
        />
      ) : items.length === 0 ? (
        <EmptyItemsState />
      ) : (
        <ul role="list" className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
          {items.map((item) => {
            const detailHref = `/master-list/${item.broker_dealer_id}${detailHrefSuffix}` as Route;
            return (
              <li key={item.broker_dealer_id} className="flex items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <Link
                    href={detailHref}
                    className="block truncate text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
                  >
                    {item.broker_dealer_name}
                  </Link>
                  <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
                    Added {formatRelativeTime(item.added_at)}
                  </p>
                </div>
                <Link
                  href={detailHref}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md border border-[rgba(99,102,241,0.3)] px-2.5 py-1 text-[11px] font-semibold text-[#6366f1] transition hover:bg-[rgba(99,102,241,0.05)]"
                >
                  Review
                  <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {!loading && !error && total > pageSize ? (
        <nav
          aria-label="Favorites pagination"
          className="mt-5 flex items-center justify-between gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] pt-4"
        >
          <button
            type="button"
            onClick={() => onPageChange(safePage - 1)}
            disabled={safePage <= 1}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            <ChevronLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Previous
          </button>
          <span className="text-[12px] tabular-nums text-[var(--text-muted,#94a3b8)]">
            Page {safePage.toLocaleString()} of {totalPages.toLocaleString()}
          </span>
          <button
            type="button"
            onClick={() => onPageChange(safePage + 1)}
            disabled={safePage >= totalPages}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            Next
            <ChevronRight className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        </nav>
      ) : null}
    </div>
  );
}

// Skeleton rows preview the eventual row shape (name + subtext + Review
// chip on the right) so the loading state doesn't visually jump when the
// fetch resolves.
function ItemsSkeleton() {
  return (
    <ul role="list" className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]" aria-busy>
      {Array.from({ length: 6 }).map((_, index) => (
        <li
          key={`items-skel-${index}`}
          className="flex items-center gap-3 py-3"
        >
          <div className="min-w-0 flex-1 space-y-2">
            <div className="h-3.5 w-2/3 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
            <div className="h-3 w-1/3 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          </div>
          <div className="h-6 w-[68px] shrink-0 animate-pulse rounded-md bg-[var(--surface-2,#f1f6fd)]" />
        </li>
      ))}
    </ul>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-4 py-8 text-center">
      <p className="text-[13px] text-[var(--text,#0f172a)]">
        Couldn&apos;t load favorites.
      </p>
      <p className="mt-1 text-[12px] text-[var(--text-muted,#94a3b8)]">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex h-[30px] items-center rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
      >
        Retry
      </button>
    </div>
  );
}

