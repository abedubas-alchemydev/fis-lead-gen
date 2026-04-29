"use client";

import { Star } from "lucide-react";
import { useState } from "react";

import type { FavoriteList } from "@/types/favorite-list";

import { DeleteListDialog } from "./delete-list-dialog";
import { ListRowMenu } from "./list-row-menu";
import { NewListButton } from "./new-list-button";
import { RenameListInput } from "./rename-list-input";

// Left rail on /my-favorites. Phase-1 layout (PR #144) preserved — row
// border, default badge, count alignment all unchanged. Phase 2 (#17)
// mounts a header strip with "+ New list", an inline rename input that
// replaces the row label when active, a per-row kebab dropdown
// (Rename / Delete), and a destructive-confirm dialog for delete.
//
// Each row is now a flex shell of (selection button + sibling kebab)
// instead of a single button — nested buttons aren't valid HTML, so we
// can't tuck the kebab inside the click target. The visual treatment of
// the row still matches phase 1.
export function FavoriteListsSidebar({
  lists,
  activeListId,
  loading,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  lists: FavoriteList[];
  activeListId: number | null;
  loading: boolean;
  onSelect: (listId: number) => void;
  onCreate: (name: string) => Promise<void>;
  onRename: (listId: number, name: string) => Promise<void>;
  onDelete: (listId: number) => Promise<void>;
}) {
  const [renamingListId, setRenamingListId] = useState<number | null>(null);
  const [deletingList, setDeletingList] = useState<FavoriteList | null>(null);

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

  return (
    <div className="space-y-3">
      <NewListButton onCreate={onCreate} />

      {lists.length === 0 ? (
        <p className="rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-3 py-4 text-[12px] leading-5 text-[var(--text-muted,#94a3b8)]">
          No lists yet.
        </p>
      ) : (
        <ul className="space-y-1" role="list">
          {lists.map((list) => {
            const isActive = list.id === activeListId;
            const isRenaming = list.id === renamingListId;
            // Negative ids are optimistic placeholders the parent inserts
            // during create; they shouldn't be selectable or kebab-able
            // until the server reconciles.
            const isPlaceholder = list.id < 0;

            if (isRenaming) {
              return (
                <li key={list.id}>
                  <div className="rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] p-2">
                    <RenameListInput
                      initialValue={list.name}
                      onSave={async (name) => {
                        await onRename(list.id, name);
                        setRenamingListId(null);
                      }}
                      onCancel={() => setRenamingListId(null)}
                    />
                  </div>
                </li>
              );
            }

            return (
              <li key={list.id}>
                <div
                  className={[
                    "group flex items-center gap-1 rounded-lg border transition",
                    isActive
                      ? "border-[rgba(99,102,241,0.35)] bg-[rgba(99,102,241,0.08)]"
                      : "border-transparent hover:border-[var(--border,rgba(30,64,175,0.1))] hover:bg-[var(--surface-2,#f1f6fd)]",
                  ].join(" ")}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(list.id)}
                    disabled={isPlaceholder}
                    aria-current={isActive ? "true" : undefined}
                    className={[
                      "flex min-w-0 flex-1 items-center justify-between gap-2 rounded-l-lg px-3 py-2 text-left text-[13px] transition",
                      isActive
                        ? "text-[var(--text,#0f172a)]"
                        : "text-[var(--text-dim,#475569)]",
                      isPlaceholder ? "cursor-progress opacity-60" : "",
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
                  {isPlaceholder ? null : (
                    <div
                      className={[
                        "pr-1 transition",
                        isActive
                          ? "opacity-100"
                          : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                      ].join(" ")}
                    >
                      <ListRowMenu
                        listName={list.name}
                        isDefault={list.is_default}
                        onRename={() => setRenamingListId(list.id)}
                        onRequestDelete={() => setDeletingList(list)}
                      />
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {deletingList ? (
        <DeleteListDialog
          listName={deletingList.name}
          itemCount={deletingList.item_count}
          onCancel={() => setDeletingList(null)}
          onConfirm={async () => {
            await onDelete(deletingList.id);
            setDeletingList(null);
          }}
        />
      ) : null}
    </div>
  );
}
