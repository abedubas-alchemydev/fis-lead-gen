"use client";

import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

const DISABLED_TOOLTIP =
  "The default Favorites list can't be renamed or deleted.";

// Kebab dropdown for a favorite-list row. Renders Rename + Delete items;
// for the default list both are disabled with a tooltip but the kebab is
// still focusable so keyboard users discover the constraint. Outside-click
// and Esc dismiss; positioning matches the multi-select-filter idiom
// (absolute, anchored to the trigger). Phase-2 (#17) only.
export function ListRowMenu({
  listName,
  isDefault,
  onRename,
  onRequestDelete,
}: {
  listName: string;
  isDefault: boolean;
  onRename: () => void;
  onRequestDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDocumentMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((next) => !next);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for ${listName}`}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text-dim,#475569)] focus-visible:bg-[var(--surface-2,#f1f6fd)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(99,102,241,0.3)]"
      >
        <MoreHorizontal className="h-4 w-4" strokeWidth={2} aria-hidden />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+4px)] z-20 min-w-[160px] overflow-hidden rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] py-1 shadow-[0_8px_20px_-12px_rgba(15,23,42,0.25)]"
        >
          <MenuItem
            label="Rename"
            icon={<Pencil className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />}
            disabled={isDefault}
            tooltip={isDefault ? DISABLED_TOOLTIP : undefined}
            onSelect={() => {
              setOpen(false);
              onRename();
            }}
          />
          <MenuItem
            label="Delete"
            icon={<Trash2 className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />}
            disabled={isDefault}
            tooltip={isDefault ? DISABLED_TOOLTIP : undefined}
            destructive
            onSelect={() => {
              setOpen(false);
              onRequestDelete();
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function MenuItem({
  label,
  icon,
  disabled,
  destructive,
  tooltip,
  onSelect,
}: {
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  destructive?: boolean;
  tooltip?: string;
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
