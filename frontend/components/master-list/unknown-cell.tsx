"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";

import { RefreshFinancialsButton } from "@/components/master-list/detail/refresh-financials-button";
import { unknownReasonShort } from "@/lib/format";
import type { UnknownReason } from "@/lib/types";

// Notes can run long when the BE captures the full extraction narrative.
// 240 chars keeps the tooltip readable inside a table cell without
// pushing the layout. Anything longer is clipped with an ellipsis.
const NOTE_TRUNCATE = 240;

// Visual gap between the icon and the floating tooltip.
const TOOLTIP_GAP = 8;
// Minimum breathing room from the viewport edge so the tooltip never
// kisses the browser chrome on small screens.
const VIEWPORT_PADDING = 8;
// Tooltip max width has to match the rendered class (max-w-xs ≈ 320px)
// so the JS positioner clamps to the same box.
const TOOLTIP_MAX_WIDTH = 320;

interface UnknownCellProps {
  reason?: UnknownReason | null;
  // Text rendered in place of the missing value. Defaults to "Unknown" so
  // most callers can drop UnknownCell in without thinking; pass `—` (or a
  // styled span) to match the surrounding cell's existing placeholder.
  fallback?: ReactNode;
  // Shrinks the icon + tooltip a touch for inline use inside pill rows
  // or stat cards where the standard size feels heavy.
  compact?: boolean;
  // Opt-in: when set and the reason category is `not_yet_extracted`,
  // render a "Refresh financials" button next to the info icon. Callers
  // pass this only for cells anchored to financial-pipeline fields
  // (latest_net_capital, latest_excess_net_capital, yoy_growth,
  // health_status). Other UnknownCell instances (clearing arrangements,
  // executive contacts, etc.) leave it undefined so the button stays
  // off — those failure modes have their own remediation paths.
  refreshFinancials?: { firmId: number };
}

interface TooltipCoords {
  top: number;
  left: number;
  placement: "top" | "bottom";
}

// Inline cell that explains why a master-list / firm-detail field is
// "Unknown". The tooltip used to be a CSS-only group-hover sibling, but
// the master-list table is wrapped in `overflow-hidden` + `overflow-x-auto`
// containers that clipped the tooltip on rows near the top edge of the
// table card. The portal-based approach renders the tooltip into
// document.body, escapes every overflow context, and auto-flips below
// the icon when there isn't room above.
export function UnknownCell({
  reason,
  fallback = "Unknown",
  compact = false,
  refreshFinancials,
}: UnknownCellProps) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<TooltipCoords | null>(null);
  const [mounted, setMounted] = useState(false);

  // createPortal needs document.body, which is undefined during SSR.
  useEffect(() => {
    setMounted(true);
  }, []);

  const updatePosition = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const buttonRect = button.getBoundingClientRect();
    const tooltipRect = tooltipRef.current?.getBoundingClientRect();
    const tooltipHeight = tooltipRect?.height ?? 80;
    const tooltipWidth = Math.min(
      tooltipRect?.width ?? TOOLTIP_MAX_WIDTH,
      TOOLTIP_MAX_WIDTH,
    );

    const spaceAbove = buttonRect.top;
    const spaceBelow = window.innerHeight - buttonRect.bottom;
    const placement: "top" | "bottom" =
      spaceAbove >= tooltipHeight + TOOLTIP_GAP + VIEWPORT_PADDING ||
      spaceAbove >= spaceBelow
        ? "top"
        : "bottom";

    const top =
      placement === "top"
        ? buttonRect.top - tooltipHeight - TOOLTIP_GAP
        : buttonRect.bottom + TOOLTIP_GAP;

    const idealLeft = buttonRect.left + buttonRect.width / 2 - tooltipWidth / 2;
    const left = Math.max(
      VIEWPORT_PADDING,
      Math.min(idealLeft, window.innerWidth - tooltipWidth - VIEWPORT_PADDING),
    );

    setCoords({ top, left, placement });
  }, []);

  // Re-measure synchronously after the tooltip mounts so the first paint
  // already reflects the correct placement (no flicker).
  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
  }, [open, updatePosition]);

  // Keep the tooltip pinned to the icon while the user scrolls or resizes
  // — the icon lives inside an overflow-x-auto table that frequently
  // moves under the cursor.
  useEffect(() => {
    if (!open) return;
    const handler = () => updatePosition();
    window.addEventListener("scroll", handler, true);
    window.addEventListener("resize", handler);
    return () => {
      window.removeEventListener("scroll", handler, true);
      window.removeEventListener("resize", handler);
    };
  }, [open, updatePosition]);

  if (!reason) {
    return (
      <span className="text-[var(--text-muted,#94a3b8)]">{fallback}</span>
    );
  }

  const shortLabel = unknownReasonShort(reason);
  // BE prepends `[Triggered by missing: <field>]` to `note` so the tooltip
  // can lead with the specific column that triggered the cluster-level
  // reason. Strip it from the note body and surface it as a separate line.
  const triggerMatch =
    reason.note?.match(/^\[Triggered by missing:\s*([^\]]+)\]\s*/) ?? null;
  const triggerField = triggerMatch ? triggerMatch[1].trim() : null;
  const rawNote = triggerMatch
    ? reason.note!.slice(triggerMatch[0].length)
    : reason.note;
  const note =
    rawNote && rawNote.length > NOTE_TRUNCATE
      ? rawNote.slice(0, NOTE_TRUNCATE) + "…"
      : rawNote;

  const iconSize = compact ? "h-3.5 w-3.5" : "h-3.5 w-3.5";
  // Compact mode used to dim to 70% but the icon then disappears next to
  // the dark "Unknown" pill in the master-list table — users couldn't
  // tell there was anything clickable. Keep full opacity in compact;
  // larger contexts already have room to breathe and stay at 70%.
  const iconOpacity = compact ? "opacity-100" : "opacity-70";
  const showRefreshButton =
    refreshFinancials !== undefined && reason.category === "not_yet_extracted";

  const tooltipNode =
    open && mounted && coords
      ? createPortal(
          <div
            ref={tooltipRef}
            role="tooltip"
            style={{
              position: "fixed",
              top: coords.top,
              left: coords.left,
              maxWidth: TOOLTIP_MAX_WIDTH,
              zIndex: 9999,
            }}
            className="pointer-events-none w-max rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-left text-[12px] leading-5 text-[var(--text,#0f172a)] shadow-[0_10px_28px_rgba(15,23,42,0.18)]"
          >
            <span className="block font-semibold">{shortLabel}</span>
            {triggerField ? (
              <span className="mt-1 block text-[11px] font-medium text-[var(--text-dim,#475569)]">
                Missing field: <span className="font-mono">{triggerField}</span>
              </span>
            ) : null}
            {note ? (
              <span className="mt-1 block text-[11px] text-[var(--text-dim,#475569)]">
                {note}
              </span>
            ) : null}
          </div>,
          document.body,
        )
      : null;

  return (
    <span className="relative inline-flex items-center gap-1 text-[var(--text-muted,#94a3b8)]">
      {fallback}
      <button
        ref={buttonRef}
        type="button"
        aria-label={`Why is this Unknown? ${shortLabel}`}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="inline-flex cursor-help items-center rounded-full p-0.5 text-[var(--text-muted,#94a3b8)] outline-none transition hover:text-[var(--text-dim,#475569)] focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]"
      >
        <Info className={`${iconSize} ${iconOpacity}`} strokeWidth={2} aria-hidden />
      </button>
      {showRefreshButton ? (
        <RefreshFinancialsButton
          firmId={refreshFinancials.firmId}
          compact={compact}
        />
      ) : null}
      {tooltipNode}
    </span>
  );
}
