"use client";

import { Check, ChevronDown, Heart, Loader2, Plus } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import {
  ApiError,
  addAdvisorToList,
  addFirmToList,
  addReportingOwnerToList,
  createFavoriteList,
  getListsForAdvisor,
  getListsForFirm,
  getListsForReportingOwner,
  removeAdvisorFromList,
  removeFirmFromList,
  removeReportingOwnerFromList,
} from "@/lib/api";
import type {
  FavoriteListEntityType,
  FavoriteListWithMembership,
} from "@/types/favorite-list";

const MAX_NEW_LIST_NAME_LENGTH = 80;

// #17 phase 3 — picker dropdown that lets a user add/remove a firm
// to/from any of their favorite lists from anywhere they encounter
// the firm (master-list rows, firm-detail header). Phase 1 shipped
// the read-only multi-list view; phase 2 made the lists writable;
// this surfaces the writable surface outside /my-favorites.
//
// Behaviour contract:
//   - Lazy fetch on first open; cached for the component's lifetime
//   - Optimistic checkbox flip; rollback + toast on server error
//   - Default list rendered first and visually distinguished — that
//     row IS the "default list quick-toggle" the spec calls for, so
//     the heart's existing one-click affordance reads as one click
//     to open + one click to toggle the default checkbox.
//   - Outside-click closes (mousedown handler — same pattern as
//     multi-select-filter.tsx)
//   - For variant="detail"/"row-heart", the trigger heart fills when
//     the firm appears on ANY of the user's lists (default OR custom),
//     with `initialFavorited` as a pre-fetch seed so the heart isn't
//     misleading on first paint.
//
// IDs are integers (FavoriteList.id: number) — see the comment in
// frontend/types/favorite-list.ts.

export type ListPickerVariant = "row" | "detail" | "row-heart";

export interface ListPickerProps {
  // The id of the entity the picker is saving. Combined with
  // ``entityType`` it routes to the right BE endpoint. Pre-existing call
  // sites that pass only ``firmId`` keep working — entityType defaults to
  // "broker_dealer". For entityType="reporting_owner" this is the
  // reporting_owners surrogate id, which may be 0 until the insider is
  // first favorited — use ``reportingOwnerCik`` for the add path.
  firmId: number;
  variant: ListPickerVariant;
  // Discriminates the four favoritable entity types. Defaults to
  // "broker_dealer" so master-list / firm-detail callers don't change.
  entityType?: FavoriteListEntityType;
  // Reporting-owner (insider) CIK. Required when
  // entityType="reporting_owner": the membership lookup and the add path
  // key on CIK because the surrogate id is lazy-created on first favorite.
  reportingOwnerCik?: string;
  // Seeds the heart fill on variant="detail"/"row-heart" before the
  // picker has fetched, mirroring any-list membership. Ignored on
  // variant="row" (the Save pill has no filled state).
  initialFavorited?: boolean;
}

export function ListPicker({
  firmId,
  variant,
  entityType = "broker_dealer",
  reportingOwnerCik,
  initialFavorited = false,
}: ListPickerProps) {
  // Reporting owners are lazy-created, so a row may not have a surrogate
  // id until its first favorite. Seed from ``firmId`` and capture the id
  // the add endpoint resolves so a later un-favorite can DELETE by id. A
  // ref (not state) keeps the toggle callbacks free of stale closures.
  const reportingOwnerIdRef = useRef(firmId);
  useEffect(() => {
    if (firmId) reportingOwnerIdRef.current = firmId;
  }, [firmId]);

  const fetchLists = useCallback(
    () =>
      entityType === "reporting_owner"
        ? getListsForReportingOwner(reportingOwnerCik ?? "")
        : entityType === "advisor"
          ? getListsForAdvisor(firmId)
          : getListsForFirm(firmId),
    [entityType, firmId, reportingOwnerCik],
  );
  const addToList = useCallback(
    async (listId: number) => {
      if (entityType === "reporting_owner") {
        const res = await addReportingOwnerToList(listId, reportingOwnerCik ?? "");
        reportingOwnerIdRef.current = res.reporting_owner_id;
        return;
      }
      if (entityType === "advisor") {
        await addAdvisorToList(listId, firmId);
        return;
      }
      await addFirmToList(listId, firmId);
    },
    [entityType, firmId, reportingOwnerCik],
  );
  const removeFromList = useCallback(
    (listId: number) =>
      entityType === "reporting_owner"
        ? removeReportingOwnerFromList(listId, reportingOwnerIdRef.current)
        : entityType === "advisor"
          ? removeAdvisorFromList(listId, firmId)
          : removeFirmFromList(listId, firmId),
    [entityType, firmId],
  );
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<FavoriteListWithMembership[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<ReadonlySet<number>>(
    () => new Set(),
  );
  // Inline "+ New list" footer state. Mirrors the create form in
  // my-favorites/new-list-button.tsx so users don't have to bounce
  // back to /my-favorites just to create a list before saving a firm
  // into it. On submit: createFavoriteList → addFirmToList → append
  // the new list with is_member=true so the checkbox renders ticked.
  const [creatingList, setCreatingList] = useState(false);
  const [newListValue, setNewListValue] = useState("");
  const [newListError, setNewListError] = useState<string | null>(null);
  const [newListSubmitting, setNewListSubmitting] = useState(false);
  const newListInputRef = useRef<HTMLInputElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const toast = useToast();

  // Portal mount + computed fixed-positioning. The popover used to render
  // as a `position: absolute` child of the trigger, which got clipped by
  // ancestor `overflow: hidden` (the master-list table card wraps every
  // row in `overflow-hidden rounded-2xl`). Portaling to document.body and
  // anchoring with `position: fixed` to the trigger's bounding rect lets
  // the popover escape every clipping boundary.
  const [mounted, setMounted] = useState(false);
  const [position, setPosition] = useState<{
    top: number;
    left?: number;
    right?: number;
  } | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const computePosition = useCallback(() => {
    const trigger = rootRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const top = rect.bottom + 8; // matches the prior `mt-2` spacing
    if (variant === "row" || variant === "row-heart") {
      // Anchor popover's right edge to trigger's right edge — keeps the
      // 18rem-wide panel from drifting off the viewport's right side.
      setPosition({ top, right: window.innerWidth - rect.right });
    } else {
      setPosition({ top, left: rect.left });
    }
  }, [variant]);

  // Outside-click closes. mousedown so checkbox-toggle clicks below win.
  // Both the trigger root AND the portaled popover panel must be excluded
  // — the panel lives in document.body, outside rootRef's DOM subtree.
  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      const target = event.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  // While open, keep the popover glued to the trigger across viewport
  // resizes and scrolls in any ancestor (capture phase catches nested
  // scroll containers like the table card's `overflow-x-auto`).
  useEffect(() => {
    if (!open) return;
    function onUpdate() {
      computePosition();
    }
    window.addEventListener("resize", onUpdate);
    window.addEventListener("scroll", onUpdate, true);
    return () => {
      window.removeEventListener("resize", onUpdate);
      window.removeEventListener("scroll", onUpdate, true);
    };
  }, [open, computePosition]);

  const togglePicker = useCallback(() => {
    if (!open) computePosition();
    setOpen((v) => !v);
  }, [open, computePosition]);

  // Lazy fetch on first open. AbortController so a quick close
  // doesn't paint stale data.
  useEffect(() => {
    if (!open || lists !== null) return;
    const controller = new AbortController();
    let active = true;

    setFetchError(null);
    fetchLists()
      .then((data) => {
        if (!active || controller.signal.aborted) return;
        // Sort: default first, then by created_at asc — same ordering
        // /my-favorites uses so the picker matches.
        const sorted = [...data].sort((a, b) => {
          if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
          return a.created_at.localeCompare(b.created_at);
        });
        setLists(sorted);
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setFetchError(
          err instanceof Error ? err.message : "Couldn't load your lists.",
        );
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [open, lists, fetchLists]);

  // Any-list membership drives the trigger's filled-heart state on
  // variant="detail" / "row-heart" — a firm pinned to any of the user's
  // lists (default OR custom) reads as "favorited" overall. Falls back
  // to the seed before the first fetch.
  const isFavorited = useMemo(() => {
    if (lists === null) return initialFavorited;
    return lists.some((l) => l.is_member);
  }, [lists, initialFavorited]);

  const handleToggle = useCallback(
    async (list: FavoriteListWithMembership) => {
      if (pendingIds.has(list.id)) return;

      const next = !list.is_member;
      // Optimistic flip
      setLists((current) =>
        current
          ? current.map((l) =>
              l.id === list.id
                ? {
                    ...l,
                    is_member: next,
                    item_count: Math.max(0, l.item_count + (next ? 1 : -1)),
                  }
                : l,
            )
          : current,
      );
      setPendingIds((current) => {
        const updated = new Set(current);
        updated.add(list.id);
        return updated;
      });

      try {
        if (next) {
          await addToList(list.id);
        } else {
          await removeFromList(list.id);
        }
      } catch (err: unknown) {
        // Revert
        setLists((current) =>
          current
            ? current.map((l) =>
                l.id === list.id
                  ? {
                      ...l,
                      is_member: list.is_member,
                      item_count: list.item_count,
                    }
                  : l,
              )
            : current,
        );
        const message =
          err instanceof Error
            ? err.message
            : "Couldn't update list — please try again.";
        toast.error(message);
      } finally {
        setPendingIds((current) => {
          const updated = new Set(current);
          updated.delete(list.id);
          return updated;
        });
      }
    },
    [addToList, removeFromList, pendingIds, toast],
  );

  // Auto-focus the input the moment the inline form expands.
  useEffect(() => {
    if (creatingList) newListInputRef.current?.focus();
  }, [creatingList]);

  // Reset the inline form whenever the popover closes so reopening
  // never lands on a half-typed name from a prior session.
  useEffect(() => {
    if (open) return;
    setCreatingList(false);
    setNewListValue("");
    setNewListError(null);
    setNewListSubmitting(false);
  }, [open]);

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
        // Auto-add the current firm to the freshly created list — the
        // user opened this picker to save THIS firm somewhere, so
        // creating a list and not adding the firm would be a confusing
        // extra click. If the add fails after the list itself was
        // created, fall back to surfacing the list unchecked + a toast
        // so the user can retry the membership manually.
        let isMember = false;
        try {
          await addToList(created.id);
          isMember = true;
        } catch (err) {
          const message =
            err instanceof Error
              ? err.message
              : "List created, but couldn't add this firm — try the checkbox.";
          toast.error(message);
        }
        setLists((current) => {
          const newList: FavoriteListWithMembership = {
            ...created,
            is_member: isMember,
            item_count: created.item_count + (isMember ? 1 : 0),
          };
          if (current === null) return [newList];
          return [...current, newList];
        });
        toast.success(`Created '${created.name}'.`);
        setNewListValue("");
        setCreatingList(false);
      } catch (err) {
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
    [addToList, newListSubmitting, newListValue, toast],
  );

  const triggerLabel = useMemo(() => {
    if (variant === "detail" || variant === "row-heart") {
      return isFavorited
        ? "Open favorite-list picker (favorited)"
        : "Open favorite-list picker";
    }
    return "Save to a list";
  }, [variant, isFavorited]);

  const popoverPanel =
    open && position ? (
      <div
        ref={popoverRef}
        style={{
          position: "fixed",
          top: position.top,
          ...(position.left !== undefined ? { left: position.left } : {}),
          ...(position.right !== undefined ? { right: position.right } : {}),
        }}
        className="z-[60] w-72 overflow-hidden rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
        role="dialog"
        aria-label="Favorite-list picker"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
            Save to lists
          </p>
        </div>

        {lists === null && fetchError === null ? (
          <div className="flex items-center gap-2 px-3 py-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <Loader2
              className="h-3.5 w-3.5 animate-spin"
              strokeWidth={2.5}
            />
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
              const checked = list.is_member;
              const pending = pendingIds.has(list.id);
              return (
                <li key={list.id}>
                  <label
                    className={`flex cursor-pointer items-center gap-2.5 px-3 py-2 text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)] ${
                      checked
                        ? "text-[var(--text,#0f172a)]"
                        : "text-[var(--text-dim,#475569)]"
                    } ${pending ? "opacity-60" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={pending}
                      onChange={() => void handleToggle(list)}
                      className="h-4 w-4 shrink-0 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-[var(--accent,#6366f1)] focus:ring-[var(--accent,#6366f1)]"
                    />
                    <span className="min-w-0 flex-1 truncate">
                      {list.name}
                      {list.is_default ? (
                        <span className="ml-2 inline-flex items-center rounded-full bg-[rgba(99,102,241,0.12)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[#4338ca]">
                          Default
                        </span>
                      ) : null}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-[var(--text-muted,#94a3b8)]">
                      {list.item_count.toLocaleString()}
                    </span>
                    {pending ? (
                      <Loader2
                        className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--text-muted,#94a3b8)]"
                        strokeWidth={2.5}
                      />
                    ) : checked ? (
                      <Check
                        className="h-3.5 w-3.5 shrink-0 text-[var(--accent,#6366f1)]"
                        strokeWidth={2.5}
                      />
                    ) : null}
                  </label>
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
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={closeNewListForm}
                  disabled={newListSubmitting}
                >
                  Cancel
                </Button>
                <button
                  type="submit"
                  disabled={
                    newListSubmitting || newListValue.trim().length === 0
                  }
                  className={clsx(
                    buttonBase,
                    buttonSizes.sm,
                    "border border-[rgba(99,102,241,0.4)] bg-[rgba(99,102,241,0.08)] text-[#4338ca] hover:bg-[rgba(99,102,241,0.14)]",
                  )}
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
              className="flex w-full items-center gap-2 px-3 py-2.5 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <Plus className="h-3.5 w-3.5 shrink-0" strokeWidth={2.5} aria-hidden />
              New list
            </button>
          )}
        </div>
      </div>
    ) : null;

  return (
    <div ref={rootRef} className="relative inline-flex">
      {variant === "detail" ? (
        <DetailTrigger
          open={open}
          onClick={togglePicker}
          favorited={isFavorited}
          ariaLabel={triggerLabel}
        />
      ) : variant === "row-heart" ? (
        <RowHeartTrigger
          open={open}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            togglePicker();
          }}
          favorited={isFavorited}
          ariaLabel={triggerLabel}
        />
      ) : (
        <RowTrigger
          open={open}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            togglePicker();
          }}
          ariaLabel={triggerLabel}
        />
      )}

      {mounted && popoverPanel
        ? createPortal(popoverPanel, document.body)
        : null}
    </div>
  );
}

// Detail-page trigger — heart icon button styled to match the
// firm-detail header so the picker reads as a natural evolution of
// the legacy single-favorite affordance.
function DetailTrigger({
  open,
  onClick,
  favorited,
  ariaLabel,
}: {
  open: boolean;
  onClick: () => void;
  favorited: boolean;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={ariaLabel}
      title={ariaLabel}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition ${
        favorited
          ? "border-red-200 bg-red-500/15 text-red-500 hover:bg-red-500/20"
          : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
      }`}
    >
      <Heart
        className="h-5 w-5"
        strokeWidth={2}
        fill={favorited ? "currentColor" : "none"}
        aria-hidden
      />
    </button>
  );
}

// Master-list row trigger — small "Save" pill with a chevron. Sized
// to fit comfortably alongside the firm name in the row's name cell.
function RowTrigger({
  open,
  onClick,
  ariaLabel,
}: {
  open: boolean;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={ariaLabel}
      title={ariaLabel}
      className={`inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] font-semibold uppercase tracking-[0.06em] transition ${
        open
          ? "border-[var(--accent,#6366f1)] bg-[rgba(99,102,241,0.08)] text-[var(--accent,#6366f1)]"
          : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
      }`}
    >
      <Heart className="h-3 w-3" strokeWidth={2.5} aria-hidden />
      Save
      <ChevronDown className="h-3 w-3" strokeWidth={2.5} aria-hidden />
    </button>
  );
}

// Compact heart trigger for feed-style rows (e.g. /investors, which has
// no row checkboxes). Like DetailTrigger but sized to sit inline next to
// a row's action buttons; the fill follows ``favorited`` so an insider's
// saved state reads at a glance. stopPropagation guards against any
// row-level click handler swallowing the toggle.
function RowHeartTrigger({
  open,
  onClick,
  favorited,
  ariaLabel,
}: {
  open: boolean;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  favorited: boolean;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={ariaLabel}
      title={ariaLabel}
      className={`inline-flex h-7 w-7 items-center justify-center rounded-full border transition ${
        favorited
          ? "border-red-200 bg-red-500/15 text-red-500 hover:bg-red-500/20"
          : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
      }`}
    >
      <Heart
        className="h-4 w-4"
        strokeWidth={2}
        fill={favorited ? "currentColor" : "none"}
        aria-hidden
      />
    </button>
  );
}
