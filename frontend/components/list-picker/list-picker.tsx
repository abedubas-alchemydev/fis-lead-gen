"use client";

import { Check, ChevronDown, Heart, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useToast } from "@/components/ui/use-toast";
import {
  addFirmToList,
  getListsForFirm,
  removeFirmFromList,
} from "@/lib/api";
import type { FavoriteListWithMembership } from "@/types/favorite-list";

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
//   - For variant="detail", the trigger heart fills based on the
//     default list's `is_member` once the picker has fetched, with
//     `initialDefaultMember` as a pre-fetch seed so the heart isn't
//     misleading on first paint.
//
// IDs are integers (FavoriteList.id: number) — see the comment in
// frontend/types/favorite-list.ts.

export type ListPickerVariant = "row" | "detail";

export interface ListPickerProps {
  firmId: number;
  variant: ListPickerVariant;
  // Seeds the heart fill on variant="detail" before the picker has
  // fetched. Read from BrokerDealerProfileResponse.is_favorited which
  // mirrors default-list membership for the legacy single-favorite
  // surface. Ignored on variant="row".
  initialDefaultMember?: boolean;
}

export function ListPicker({
  firmId,
  variant,
  initialDefaultMember = false,
}: ListPickerProps) {
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<FavoriteListWithMembership[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<ReadonlySet<number>>(
    () => new Set(),
  );
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
    if (variant === "row") {
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
    getListsForFirm(firmId)
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
  }, [open, lists, firmId]);

  // Default-list membership drives the trigger's filled-heart state on
  // variant="detail". Falls back to the seed before the first fetch.
  const defaultIsMember = useMemo(() => {
    if (lists === null) return initialDefaultMember;
    const def = lists.find((l) => l.is_default);
    return def ? def.is_member : initialDefaultMember;
  }, [lists, initialDefaultMember]);

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
          await addFirmToList(list.id, firmId);
        } else {
          await removeFirmFromList(list.id, firmId);
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
    [firmId, pendingIds, toast],
  );

  const triggerLabel = useMemo(() => {
    if (variant === "detail") {
      return defaultIsMember
        ? "Open favorite-list picker (favorited)"
        : "Open favorite-list picker";
    }
    return "Save to a list";
  }, [variant, defaultIsMember]);

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
            You have no favorite lists yet. Create one in My Favorites.
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
      </div>
    ) : null;

  return (
    <div ref={rootRef} className="relative inline-flex">
      {variant === "detail" ? (
        <DetailTrigger
          open={open}
          onClick={togglePicker}
          favorited={defaultIsMember}
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
