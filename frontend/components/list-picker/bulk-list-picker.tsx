"use client";

import { Loader2, Plus } from "lucide-react";
import {
  type FormEvent,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { useToast } from "@/components/ui/use-toast";
import {
  ApiError,
  type AddFirmsToListBatchResponse,
  addAdvisorsToListBatch,
  addFirmsToListBatch,
  createFavoriteList,
  getFavoriteLists,
} from "@/lib/api";
import type {
  FavoriteList,
  FavoriteListEntityType,
} from "@/types/favorite-list";

const MAX_NEW_LIST_NAME_LENGTH = 80;

// Bulk variant of list-picker.tsx — opens from the master-list bulk-action
// bar with a set of selected firm IDs and adds all of them to the chosen
// list in a single request. Add-only by design: bulk operations resolve to
// "perform action on N firms" rather than "mirror N firms' membership", so
// no per-list checkboxes / tri-state UI.

export interface BulkListPickerProps {
  selectedIds: number[];
  // The picker portals to body and positions itself by the bounding rect of
  // this trigger so it survives the table card's `overflow: hidden`.
  anchorRef: RefObject<HTMLButtonElement | null>;
  onAdded: (listName: string, response: AddFirmsToListBatchResponse) => void;
  onDismiss: () => void;
  // BD vs advisor. Defaults to "broker_dealer" so the master-list call
  // site doesn't need to change.
  entityType?: FavoriteListEntityType;
}

export function BulkListPicker({
  selectedIds,
  anchorRef,
  onAdded,
  onDismiss,
  entityType = "broker_dealer",
}: BulkListPickerProps) {
  const batchAdd = useCallback(
    (listId: number) =>
      entityType === "advisor"
        ? addAdvisorsToListBatch(listId, selectedIds)
        : addFirmsToListBatch(listId, selectedIds),
    [entityType, selectedIds],
  );
  const itemNoun = entityType === "advisor" ? "advisor" : "firm";
  const [lists, setLists] = useState<FavoriteList[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [pendingListId, setPendingListId] = useState<number | null>(null);
  const [creatingList, setCreatingList] = useState(false);
  const [newListValue, setNewListValue] = useState("");
  const [newListError, setNewListError] = useState<string | null>(null);
  const [newListSubmitting, setNewListSubmitting] = useState(false);
  const newListInputRef = useRef<HTMLInputElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  const [mounted, setMounted] = useState(false);
  const [position, setPosition] = useState<{ top: number; right: number } | null>(
    null,
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  const computePosition = useCallback(() => {
    const trigger = anchorRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    setPosition({
      top: rect.bottom + 8,
      right: window.innerWidth - rect.right,
    });
  }, [anchorRef]);

  useEffect(() => {
    computePosition();
  }, [computePosition]);

  useEffect(() => {
    function onUpdate() {
      computePosition();
    }
    window.addEventListener("resize", onUpdate);
    window.addEventListener("scroll", onUpdate, true);
    return () => {
      window.removeEventListener("resize", onUpdate);
      window.removeEventListener("scroll", onUpdate, true);
    };
  }, [computePosition]);

  // Outside-click closes. mousedown so list-row clicks land first. Both the
  // popover panel AND the anchoring trigger button are excluded — the
  // trigger lives in the parent and clicking it is "toggle", not "dismiss".
  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      const target = event.target as Node;
      if (popoverRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onDismiss();
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, [anchorRef, onDismiss]);

  useEffect(() => {
    if (lists !== null) return;
    let active = true;
    setFetchError(null);
    getFavoriteLists()
      .then((data) => {
        if (!active) return;
        const sorted = [...data].sort((a, b) => {
          if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
          return a.created_at.localeCompare(b.created_at);
        });
        setLists(sorted);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setFetchError(
          err instanceof Error ? err.message : "Couldn't load your lists.",
        );
      });
    return () => {
      active = false;
    };
  }, [lists]);

  useEffect(() => {
    if (creatingList) newListInputRef.current?.focus();
  }, [creatingList]);

  const buildSuccessMessage = useCallback(
    (
      listName: string,
      response: AddFirmsToListBatchResponse,
    ): { variant: "success" | "error"; body: string } => {
      const { added, skipped_existing, skipped_unknown } = response;
      // No items actually landed AND we couldn't find the rest — tell the
      // user something went wrong rather than masking it as a success.
      if (added === 0 && skipped_unknown.length > 0 && skipped_existing === 0) {
        return {
          variant: "error",
          body: `${skipped_unknown.length} ${itemNoun}${
            skipped_unknown.length === 1 ? "" : "s"
          } could not be found.`,
        };
      }
      const parts: string[] = [
        `Added ${added} ${itemNoun}${added === 1 ? "" : "s"} to '${listName}'`,
      ];
      const trailers: string[] = [];
      if (skipped_existing > 0) {
        trailers.push(`${skipped_existing} already in list`);
      }
      if (skipped_unknown.length > 0) {
        trailers.push(
          `${skipped_unknown.length} could not be found`,
        );
      }
      if (trailers.length > 0) {
        parts.push(` (${trailers.join(", ")})`);
      }
      return { variant: "success", body: parts.join("") };
    },
    [itemNoun],
  );

  const handlePickList = useCallback(
    async (list: FavoriteList) => {
      if (pendingListId !== null) return;
      setPendingListId(list.id);
      try {
        const response = await batchAdd(list.id);
        const { variant, body } = buildSuccessMessage(list.name, response);
        if (variant === "success") {
          toast.success(body);
        } else {
          toast.error(body);
        }
        onAdded(list.name, response);
      } catch (err: unknown) {
        const message =
          err instanceof Error
            ? err.message
            : `Couldn't add ${itemNoun}s to the list.`;
        toast.error(message);
      } finally {
        setPendingListId(null);
      }
    },
    [batchAdd, buildSuccessMessage, itemNoun, onAdded, pendingListId, toast],
  );

  const closeNewListForm = useCallback(() => {
    setCreatingList(false);
    setNewListValue("");
    setNewListError(null);
    setNewListSubmitting(false);
  }, []);

  const handleCreateList = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (newListSubmitting) return;
      const trimmed = newListValue.trim();
      if (trimmed.length === 0) {
        setNewListError("Name can't be empty.");
        return;
      }
      if (trimmed.length > MAX_NEW_LIST_NAME_LENGTH) {
        setNewListError(
          `Name must be ${MAX_NEW_LIST_NAME_LENGTH} characters or fewer.`,
        );
        return;
      }
      setNewListSubmitting(true);
      setNewListError(null);
      try {
        const created = await createFavoriteList(trimmed);
        try {
          const response = await batchAdd(created.id);
          toast.success(
            `Created '${created.name}' and added ${response.added} ${itemNoun}${
              response.added === 1 ? "" : "s"
            }.`,
          );
          onAdded(created.name, response);
        } catch (err: unknown) {
          const message =
            err instanceof Error
              ? err.message
              : `List created, but couldn't add ${itemNoun}s — try selecting '${created.name}' from the picker.`;
          toast.error(message);
          // Append the empty list so the user sees their successful create
          // even though the add leg failed.
          setLists((current) => (current ? [...current, created] : [created]));
        }
        setNewListValue("");
        setCreatingList(false);
      } catch (err: unknown) {
        const message =
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : null;
        setNewListError(message || "Couldn't create list.");
      } finally {
        setNewListSubmitting(false);
      }
    },
    [batchAdd, itemNoun, newListSubmitting, newListValue, onAdded, toast],
  );

  const popoverPanel = useMemo(() => {
    if (!position) return null;
    return (
      <div
        ref={popoverRef}
        style={{
          position: "fixed",
          top: position.top,
          right: position.right,
        }}
        className="z-[60] w-72 overflow-hidden rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
        role="dialog"
        aria-label="Bulk save to favorite list"
      >
        <div className="border-b border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
            Save {selectedIds.length} {itemNoun}
            {selectedIds.length === 1 ? "" : "s"} to…
          </p>
        </div>

        {lists === null && fetchError === null ? (
          <div className="flex items-center gap-2 px-3 py-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2.5} />
            Loading…
          </div>
        ) : null}

        {fetchError !== null ? (
          <div className="px-3 py-3 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
            {fetchError}
          </div>
        ) : null}

        {lists !== null && lists.length === 0 ? (
          <div className="px-3 py-4 text-[12px] text-[var(--text-muted,#94a3b8)]">
            You have no favorite lists yet — create one below.
          </div>
        ) : null}

        {lists !== null && lists.length > 0 ? (
          <ul role="listbox" className="max-h-72 overflow-auto py-1">
            {lists.map((list) => {
              const pending = pendingListId === list.id;
              const disabled = pendingListId !== null && !pending;
              return (
                <li key={list.id}>
                  <button
                    type="button"
                    onClick={() => void handlePickList(list)}
                    disabled={disabled || pending}
                    className={`flex w-full cursor-pointer items-center gap-2.5 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)] ${
                      pending || disabled
                        ? "opacity-60"
                        : "text-[var(--text,#0f172a)]"
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {list.name}
                      {list.is_default ? (
                        <span className="ml-2 inline-flex items-center rounded-full bg-[rgba(99,102,241,0.12)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[#4338ca]">
                          Default
                        </span>
                      ) : null}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-[var(--text-muted,#94a3b8)]">
                      · {list.item_count.toLocaleString()}
                    </span>
                    {pending ? (
                      <Loader2
                        className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--text-muted,#94a3b8)]"
                        strokeWidth={2.5}
                      />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}

        <div className="border-t border-[var(--border,rgba(30,64,175,0.1))]">
          {creatingList ? (
            <form
              onSubmit={handleCreateList}
              className="space-y-1.5 px-3 py-2.5"
              noValidate
            >
              <input
                ref={newListInputRef}
                type="text"
                value={newListValue}
                onChange={(event) => {
                  setNewListValue(event.target.value);
                  if (newListError) setNewListError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.preventDefault();
                    closeNewListForm();
                  }
                }}
                placeholder="New list name"
                maxLength={MAX_NEW_LIST_NAME_LENGTH}
                aria-label="New list name"
                aria-invalid={newListError ? true : undefined}
                disabled={newListSubmitting}
                className="block w-full rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1.5 text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:border-[var(--accent,#6366f1)] focus:outline-none focus:ring-2 focus:ring-[rgba(99,102,241,0.2)] disabled:opacity-60"
              />
              <div className="flex items-center justify-end gap-1.5">
                <button
                  type="button"
                  onClick={closeNewListForm}
                  disabled={newListSubmitting}
                  className="inline-flex h-[26px] items-center rounded-md border border-transparent px-2 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    newListSubmitting || newListValue.trim().length === 0
                  }
                  className="inline-flex h-[26px] items-center rounded-md border border-[rgba(99,102,241,0.4)] bg-[rgba(99,102,241,0.08)] px-2.5 text-[11px] font-semibold text-[#4338ca] transition hover:bg-[rgba(99,102,241,0.14)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {newListSubmitting ? "Saving…" : "Save"}
                </button>
              </div>
              {newListError ? (
                <p
                  role="alert"
                  className="text-[11px] leading-4 text-[var(--red,#dc2626)]"
                >
                  {newListError}
                </p>
              ) : null}
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreatingList(true)}
              disabled={pendingListId !== null}
              className="flex w-full items-center gap-2 px-3 py-2.5 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Plus
                className="h-3.5 w-3.5 shrink-0"
                strokeWidth={2.5}
                aria-hidden
              />
              New list
            </button>
          )}
        </div>
      </div>
    );
  }, [
    closeNewListForm,
    creatingList,
    fetchError,
    handleCreateList,
    handlePickList,
    itemNoun,
    lists,
    newListError,
    newListSubmitting,
    newListValue,
    pendingListId,
    position,
    selectedIds.length,
  ]);

  if (!mounted || !popoverPanel) return null;
  return createPortal(popoverPanel, document.body);
}
