"use client";

import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SectionPanel } from "@/components/ui/section-panel";
import { useToast } from "@/components/ui/use-toast";
import { getFavoriteLists } from "@/lib/api";
import type { FavoriteList } from "@/types/favorite-list";

import { FavoriteListItemsPane } from "./favorite-list-items-pane";
import { FavoriteListsSidebar } from "./favorite-lists-sidebar";

const PAGE_SIZE = 20;

// Phase-1 read-only multi-list workspace at /my-favorites. Composes the
// list sidebar (left rail) + items pane (right). URL params `?list=` and
// `?page=` drive selection so back-nav, share-links, and reloads all
// restore the same view — same pattern as master-list-workspace-client.
//
// Phase 2 (next sprint) will add create / rename / delete UI once the BE
// POST/PUT/DELETE endpoints land.
export function MyFavoritesClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const toast = useToast();

  const [lists, setLists] = useState<FavoriteList[]>([]);
  const [loadingLists, setLoadingLists] = useState(true);
  const [listsError, setListsError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoadingLists(true);
    setListsError(null);

    getFavoriteLists()
      .then((response) => {
        if (!active) return;
        setLists(response);
      })
      .catch((err) => {
        if (!active) return;
        const message =
          err instanceof Error ? err.message : "Couldn't load favorite lists";
        setListsError(message);
        toast.error(message);
      })
      .finally(() => {
        if (active) setLoadingLists(false);
      });

    return () => {
      active = false;
    };
  }, [toast]);

  // Resolve the active list from the URL, falling back to the default.
  // Lists are sorted default-first by the BE (PR #140), so `lists[0]` is
  // the default when the user hasn't pinned a different one via `?list=`.
  const activeList = useMemo<FavoriteList | null>(() => {
    if (lists.length === 0) return null;
    const raw = searchParams.get("list");
    if (raw) {
      const id = Number(raw);
      const match = lists.find((list) => list.id === id);
      if (match) return match;
    }
    return lists[0] ?? null;
  }, [lists, searchParams]);

  const page = useMemo(() => {
    const raw = searchParams.get("page");
    const parsed = raw ? Number(raw) : 1;
    return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
  }, [searchParams]);

  const replaceParams = useCallback(
    (patch: { list?: number | null; page?: number | null }) => {
      const next = new URLSearchParams(searchParams.toString());
      if (patch.list !== undefined) {
        if (patch.list === null) next.delete("list");
        else next.set("list", String(patch.list));
      }
      if (patch.page !== undefined) {
        if (patch.page === null || patch.page <= 1) next.delete("page");
        else next.set("page", String(patch.page));
      }
      const query = next.toString();
      const href = (query ? `/my-favorites?${query}` : "/my-favorites") as Route;
      router.replace(href, { scroll: false });
    },
    [router, searchParams],
  );

  const handleSelectList = useCallback(
    (listId: number) => {
      // Selection resets pagination — no point landing on page 4 of the
      // previous list when switching to a new one.
      replaceParams({ list: listId, page: null });
    },
    [replaceParams],
  );

  const handlePageChange = useCallback(
    (next: number) => {
      replaceParams({ page: next });
    },
    [replaceParams],
  );

  const totalSaved = useMemo(
    () => lists.reduce((sum, list) => sum + list.item_count, 0),
    [lists],
  );

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {totalSaved.toLocaleString()} firm{totalSaved === 1 ? "" : "s"} across{" "}
          {lists.length.toLocaleString()} list
          {lists.length === 1 ? "" : "s"}
        </span>
      </div>

      <SectionPanel eyebrow="Workspace" title="Saved firms">
        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          <aside aria-label="Favorite lists">
            <FavoriteListsSidebar
              lists={lists}
              activeListId={activeList?.id ?? null}
              loading={loadingLists}
              onSelect={handleSelectList}
            />
          </aside>
          <section aria-label="Favorite list contents" className="min-w-0">
            {listsError ? (
              <p className="rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-4 py-8 text-center text-[13px] text-[var(--text-dim,#475569)]">
                Couldn&apos;t load favorite lists.
              </p>
            ) : activeList ? (
              <FavoriteListItemsPane
                activeList={activeList}
                page={page}
                pageSize={PAGE_SIZE}
                onPageChange={handlePageChange}
              />
            ) : loadingLists ? (
              <div className="space-y-2" aria-busy>
                {Array.from({ length: 6 }).map((_, index) => (
                  <div
                    key={`pane-skel-${index}`}
                    className="h-[58px] animate-pulse rounded-lg bg-[var(--surface-2,#f1f6fd)]"
                  />
                ))}
              </div>
            ) : null}
          </section>
        </div>
      </SectionPanel>
    </>
  );
}
