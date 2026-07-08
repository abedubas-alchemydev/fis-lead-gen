"use client";

import {
  Ban,
  Copy,
  KeyRound,
  MoreHorizontal,
  RotateCcw,
  Trash2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { useToast } from "@/components/ui/use-toast";
import { ApiError } from "@/lib/api";
import { formatDate, formatRelativeTime } from "@/lib/format";
import {
  deleteShare,
  listShares,
  resolveShareUrl,
  revokeShare,
  rotateSharePassword,
  unrevokeShare,
} from "@/lib/shares-api";
import type { RotatePasswordResponse, ShareSummary } from "@/types/lead-share";

import {
  ConfirmShareActionDialog,
  type ShareActionMode,
} from "./confirm-share-action-dialog";
import { CopyFieldButton } from "./copy-field-button";
import { RotatePasswordDialog } from "./rotate-password-dialog";

// Admin table at /settings/shared-links: every password-protected share
// link, with per-row lifecycle actions (copy / rotate password / revoke /
// unrevoke / delete). Mutations are optimistic — snapshot, mutate locally,
// reconcile with the server row — and revert + re-throw on failure so the
// confirm dialogs can surface ApiError.detail inline (e.g. the 409
// wrong-state responses). Success paths toast.
export function SharedLinksClient() {
  const toast = useToast();

  const [shares, setShares] = useState<ShareSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // One dialog at a time. "rotate" opens the two-step rotate flow; the
  // three ShareActionMode values open the shared confirm dialog.
  const [dialog, setDialog] = useState<
    | { kind: "rotate"; share: ShareSummary }
    | { kind: ShareActionMode; share: ShareSummary }
    | null
  >(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    listShares()
      .then((response) => {
        if (!active) return;
        setShares(response.items);
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof Error ? err.message : "Couldn't load shared links.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [reloadKey]);

  const handleCopyLink = useCallback(
    async (share: ShareSummary) => {
      try {
        await navigator.clipboard.writeText(resolveShareUrl(share));
        toast.success("Share link copied.");
      } catch {
        toast.error("Couldn't copy — clipboard access was blocked.");
      }
    },
    [toast],
  );

  // Rotate has no optimistic step (nothing row-visible changes until the
  // response arrives); the dialog renders the once-only password itself.
  const handleRotate = useCallback(
    async (share: ShareSummary): Promise<RotatePasswordResponse> => {
      const response = await rotateSharePassword(share.id);
      setShares((prev) =>
        prev.map((s) =>
          s.id === share.id
            ? { ...s, password_rotated_at: response.password_rotated_at }
            : s,
        ),
      );
      return response;
    },
    [],
  );

  const handleAction = useCallback(
    async (mode: ShareActionMode, share: ShareSummary) => {
      const previous = shares;
      if (mode === "delete") {
        setShares((prev) => prev.filter((s) => s.id !== share.id));
      } else {
        // Flip revoked_at locally, then reconcile with the server row.
        const optimisticRevokedAt =
          mode === "revoke" ? new Date().toISOString() : null;
        setShares((prev) =>
          prev.map((s) =>
            s.id === share.id ? { ...s, revoked_at: optimisticRevokedAt } : s,
          ),
        );
      }
      try {
        if (mode === "delete") {
          await deleteShare(share.id);
          toast.success(`Deleted '${share.name}'.`);
        } else {
          const updated =
            mode === "revoke"
              ? await revokeShare(share.id)
              : await unrevokeShare(share.id);
          setShares((prev) =>
            prev.map((s) => (s.id === share.id ? updated : s)),
          );
          toast.success(
            mode === "revoke"
              ? `Revoked '${share.name}'. Recipients can no longer open it.`
              : `Unrevoked '${share.name}'. The link works again.`,
          );
        }
      } catch (err) {
        setShares(previous);
        // ApiError lands inline in the confirm dialog (its catch renders
        // .detail); anything else also gets a toast for visibility.
        if (err instanceof ApiError) throw err;
        const message =
          err instanceof Error ? err.message : `Couldn't ${mode} share link.`;
        toast.error(message);
        throw err;
      }
    },
    [shares, toast],
  );

  return (
    <>
      <SectionPanel
        eyebrow="Sharing"
        title={`Shared links${
          !loading && !error && shares.length > 0
            ? ` (${shares.length.toLocaleString()})`
            : ""
        }`}
      >
        {loading ? (
          <SharesSkeleton />
        ) : error ? (
          <ErrorState
            message={error}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        ) : shares.length === 0 ? (
          <div className="py-8 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
            No shared links yet. Create one from My Favorites.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-[var(--border,rgba(30,64,175,0.1))] text-left text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  <th className="py-2 pr-3 font-semibold">Name</th>
                  <th className="py-2 pr-3 font-semibold">Link</th>
                  <th className="py-2 pr-3 text-right font-semibold">Leads</th>
                  <th className="py-2 pr-3 text-right font-semibold">Views</th>
                  <th className="py-2 pr-3 font-semibold">Last viewed</th>
                  <th className="py-2 pr-3 font-semibold">Status</th>
                  <th className="py-2 text-right font-semibold">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shares.map((share) => {
                  const revoked = share.revoked_at !== null;
                  return (
                    <tr
                      key={share.id}
                      className="border-b border-[var(--border,rgba(30,64,175,0.1))] last:border-b-0"
                    >
                      <td className="max-w-[260px] py-3 pr-3 align-top">
                        <p className="truncate font-semibold text-[var(--text,#0f172a)]">
                          {share.name}
                        </p>
                        <p className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
                          Created {formatDate(share.created_at)}
                        </p>
                      </td>
                      <td className="py-3 pr-3 align-top">
                        <span className="inline-flex items-center gap-1.5">
                          <span className="font-mono text-[12px] text-[var(--text-dim,#475569)]">
                            /share/{share.token.slice(0, 8)}
                            {share.token.length > 8 ? "…" : ""}
                          </span>
                          <CopyFieldButton
                            value={resolveShareUrl(share)}
                            label={`share URL for ${share.name}`}
                            iconOnly
                          />
                        </span>
                      </td>
                      <td className="py-3 pr-3 text-right align-top tabular-nums text-[var(--text,#0f172a)]">
                        {share.item_count.toLocaleString()}
                      </td>
                      <td className="py-3 pr-3 text-right align-top tabular-nums text-[var(--text,#0f172a)]">
                        {share.view_count.toLocaleString()}
                      </td>
                      <td className="py-3 pr-3 align-top text-[var(--text-dim,#475569)]">
                        {share.last_viewed_at
                          ? formatRelativeTime(share.last_viewed_at)
                          : "Never"}
                      </td>
                      <td className="py-3 pr-3 align-top">
                        <Pill variant={revoked ? "risk" : "healthy"}>
                          {revoked ? "Revoked" : "Active"}
                        </Pill>
                      </td>
                      <td className="py-3 text-right align-top">
                        <ShareRowMenu
                          share={share}
                          onCopyLink={() => void handleCopyLink(share)}
                          onRotate={() => setDialog({ kind: "rotate", share })}
                          onRevoke={() => setDialog({ kind: "revoke", share })}
                          onUnrevoke={() =>
                            setDialog({ kind: "unrevoke", share })
                          }
                          onDelete={() => setDialog({ kind: "delete", share })}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionPanel>

      {dialog?.kind === "rotate" ? (
        <RotatePasswordDialog
          share={dialog.share}
          onRotate={() => handleRotate(dialog.share)}
          onClose={() => setDialog(null)}
        />
      ) : dialog ? (
        <ConfirmShareActionDialog
          mode={dialog.kind}
          shareName={dialog.share.name}
          onCancel={() => setDialog(null)}
          onConfirm={async () => {
            await handleAction(dialog.kind, dialog.share);
            setDialog(null);
          }}
        />
      ) : null}
    </>
  );
}

// Kebab menu for one share row. The table body scrolls horizontally
// (`overflow-x-auto`), which would clip an absolutely-positioned child —
// so the menu portals to document.body with fixed positioning anchored to
// the trigger rect, the same escape hatch list-picker.tsx uses inside the
// master-list card.
function ShareRowMenu({
  share,
  onCopyLink,
  onRotate,
  onRevoke,
  onUnrevoke,
  onDelete,
}: {
  share: ShareSummary;
  onCopyLink: () => void;
  onRotate: () => void;
  onRevoke: () => void;
  onUnrevoke: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [position, setPosition] = useState<{
    top: number;
    right: number;
  } | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const revoked = share.revoked_at !== null;

  useEffect(() => {
    setMounted(true);
  }, []);

  const computePosition = useCallback(() => {
    const trigger = rootRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    setPosition({
      top: rect.bottom + 4,
      right: window.innerWidth - rect.right,
    });
  }, []);

  // Outside-click + Esc dismiss; reposition on resize and on scroll in any
  // ancestor (capture phase catches the table's overflow-x-auto container).
  useEffect(() => {
    if (!open) return;
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      const target = event.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onUpdate() {
      computePosition();
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onUpdate);
    window.addEventListener("scroll", onUpdate, true);
    return () => {
      document.removeEventListener("mousedown", onDocumentMouseDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onUpdate);
      window.removeEventListener("scroll", onUpdate, true);
    };
  }, [open, computePosition]);

  const select = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div ref={rootRef} className="inline-flex shrink-0">
      <button
        type="button"
        onClick={() => {
          if (!open) computePosition();
          setOpen((next) => !next);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for ${share.name}`}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text-dim,#475569)] focus-visible:bg-[var(--surface-2,#f1f6fd)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(99,102,241,0.3)]"
      >
        <MoreHorizontal className="h-4 w-4" strokeWidth={2} aria-hidden />
      </button>

      {open && mounted && position
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              style={{
                position: "fixed",
                top: position.top,
                right: position.right,
              }}
              className="z-50 min-w-[180px] overflow-hidden rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] py-1 shadow-[0_8px_20px_-12px_rgba(15,23,42,0.25)]"
            >
              <MenuItem
                label="Copy link"
                icon={<Copy className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />}
                allowCopy
                onSelect={() => select(onCopyLink)}
              />
              <MenuItem
                label="Rotate password"
                icon={
                  <KeyRound className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                }
                disabled={revoked}
                tooltip={
                  revoked
                    ? "Unrevoke this link to rotate its password."
                    : undefined
                }
                onSelect={() => select(onRotate)}
              />
              {revoked ? (
                <MenuItem
                  label="Unrevoke"
                  icon={
                    <RotateCcw
                      className="h-3.5 w-3.5"
                      strokeWidth={2}
                      aria-hidden
                    />
                  }
                  onSelect={() => select(onUnrevoke)}
                />
              ) : (
                <MenuItem
                  label="Revoke"
                  icon={<Ban className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />}
                  destructive
                  onSelect={() => select(onRevoke)}
                />
              )}
              <MenuItem
                label="Delete"
                icon={
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                }
                destructive
                onSelect={() => select(onDelete)}
              />
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

// Same menu-item styling as my-favorites/list-row-menu.tsx so kebab menus
// read identically across the app.
function MenuItem({
  label,
  icon,
  disabled,
  destructive,
  tooltip,
  allowCopy,
  onSelect,
}: {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  destructive?: boolean;
  tooltip?: string;
  allowCopy?: boolean;
  onSelect: () => void;
}) {
  const base =
    "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition";
  const enabled = destructive
    ? "text-[var(--red,#dc2626)] hover:bg-[rgba(220,38,38,0.08)]"
    : "text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]";
  const disabledCls = "cursor-not-allowed text-[var(--text-muted,#94a3b8)]";

  return (
    <button
      type="button"
      role="menuitem"
      data-allow-copy={allowCopy || undefined}
      onClick={(event) => {
        event.stopPropagation();
        if (disabled) return;
        onSelect();
      }}
      aria-disabled={disabled || undefined}
      title={tooltip}
      className={[base, disabled ? disabledCls : enabled].join(" ")}
    >
      <span className="text-[var(--text-muted,#94a3b8)]">{icon}</span>
      {label}
    </button>
  );
}

// Skeleton rows echo the settings-table idiom (doxie-usage-client): stacked
// pulse bars per row so the load state doesn't jump when data lands.
function SharesSkeleton() {
  return (
    <div aria-busy>
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={`share-skel-${index}`}
          className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
        >
          <div className="h-3.5 w-48 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-2 h-3 w-72 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
        </div>
      ))}
    </div>
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
        Couldn&apos;t load shared links.
      </p>
      <p className="mt-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
        {message}
      </p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onRetry}
        className="mt-3"
      >
        Retry
      </Button>
    </div>
  );
}
