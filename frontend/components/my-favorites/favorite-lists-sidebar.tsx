"use client";

import { Star } from "lucide-react";

import type { FavoriteList } from "@/types/favorite-list";

// Left rail on /my-favorites. Pure presentational — receives the lists
// and the active id from the parent, emits selection through onSelect.
// Default list renders with a small badge so users can see why it's
// always pinned to the top.
export function FavoriteListsSidebar({
  lists,
  activeListId,
  loading,
  onSelect,
}: {
  lists: FavoriteList[];
  activeListId: number | null;
  loading: boolean;
  onSelect: (listId: number) => void;
}) {
  if (loading) {
    return (
      <div className="space-y-2" aria-busy>
        {Array.from({ length: 4 }).map((_, index) => (
          <div
            key={`sidebar-skel-${index}`}
            className="h-[44px] animate-pulse rounded-lg bg-[var(--surface-2,#f1f6fd)]"
          />
        ))}
      </div>
    );
  }

  if (lists.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-3 py-4 text-[12px] leading-5 text-[var(--text-muted,#94a3b8)]">
        No lists yet.
      </p>
    );
  }

  return (
    <ul className="space-y-1" role="list">
      {lists.map((list) => {
        const isActive = list.id === activeListId;
        return (
          <li key={list.id}>
            <button
              type="button"
              onClick={() => onSelect(list.id)}
              aria-current={isActive ? "true" : undefined}
              className={[
                "flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-[13px] transition",
                isActive
                  ? "border-[rgba(99,102,241,0.35)] bg-[rgba(99,102,241,0.08)] text-[var(--text,#0f172a)]"
                  : "border-transparent text-[var(--text-dim,#475569)] hover:border-[var(--border,rgba(30,64,175,0.1))] hover:bg-[var(--surface-2,#f1f6fd)]",
              ].join(" ")}
            >
              <span className="flex min-w-0 flex-1 items-center gap-1.5">
                <span className="truncate font-semibold">{list.name}</span>
                {list.is_default ? (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
                    <Star className="h-2.5 w-2.5" strokeWidth={2.5} aria-hidden />
                    Default
                  </span>
                ) : null}
              </span>
              <span className="shrink-0 tabular-nums text-[11px] text-[var(--text-muted,#94a3b8)]">
                {list.item_count.toLocaleString()}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
