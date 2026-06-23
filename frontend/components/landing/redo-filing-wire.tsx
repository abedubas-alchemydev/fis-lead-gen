"use client";

import { useEffect, useState } from "react";
import { ArrowLeftRight, FileText, ShieldAlert, TrendingUp } from "lucide-react";

import { Pill } from "@/components/ui/pill";
import type { PillVariant } from "@/components/ui/pill";

// ════════════════════════════════════════════════════════════════════
// redo-filing-wire — the "it never stops" beat + the dark→light seam.
//
// Two motion pieces, both CSS-first and reduced-motion-safe:
//   (A) a horizontal TICKER TAPE of filing codes marqueeing across the top
//       (.animate-marquee-left, list rendered twice, the dup aria-hidden,
//       both edges masked with a gradient maskImage), and
//   (B) a vertical LIVE FEED where a new row tickers in at the top every
//       ~3.5s (.animate-ticker-in), pushing the list down and dropping the
//       oldest. The interval is gated behind prefers-reduced-motion (no
//       interval => static list) and always cleared on unmount.
//
// This is also the seam where the dark command hero resolves into the first
// bright working surface: a faint navy→transparent gradient continues down
// from the hero into the top of this light band.
//
// Frontend-only per the brief — every event is a hardcoded mock fixture,
// no fetch. The feed fixtures mirror the FEED_EVENTS shape already used by
// filing-feed-ticker.tsx (same firms / codes) so the two read as one system.
// ════════════════════════════════════════════════════════════════════

type FeedEvent = {
  id: string;
  icon: typeof FileText;
  variant: PillVariant;
  tag: string;
  firm: string;
  detail: string;
  ago: string;
};

// Live vertical-feed pool — rotated through, newest tickering in at the top.
// Same firms + filing vocabulary as filing-feed-ticker.tsx (Cedar Ridge /
// Atlas / Brightwater / Meridian / Northpoint / Sterling Cove / Hollis &
// Crane / Vanguard Reach) so the page speaks one consistent language.
const FEED_POOL: readonly FeedEvent[] = [
  { id: "cedar-ridge", icon: FileText, variant: "form", tag: "Form BD", firm: "Cedar Ridge Securities LLC", detail: "New broker-dealer registered", ago: "just now" },
  { id: "atlas", icon: ShieldAlert, variant: "critical", tag: "17a-11", firm: "Atlas Capital Markets", detail: "Net-capital deficiency notice", ago: "just now" },
  { id: "brightwater", icon: ArrowLeftRight, variant: "self", tag: "Clearing", firm: "Brightwater Partners", detail: "Apex → Pershing", ago: "just now" },
  { id: "meridian", icon: TrendingUp, variant: "healthy", tag: "FOCUS", firm: "Meridian Trade Group", detail: "Net capital +38% YoY", ago: "just now" },
  { id: "northpoint", icon: FileText, variant: "form", tag: "Form BD", firm: "Northpoint Execution LLC", detail: "New broker-dealer registered", ago: "just now" },
  { id: "sterling-cove", icon: ArrowLeftRight, variant: "self", tag: "Clearing", firm: "Sterling Cove Securities", detail: "Self-clearing → RBC", ago: "just now" },
  { id: "hollis-crane", icon: ShieldAlert, variant: "warning", tag: "17a-11", firm: "Hollis & Crane LLC", detail: "Early-warning threshold hit", ago: "just now" },
  { id: "vanguard-reach", icon: TrendingUp, variant: "healthy", tag: "FOCUS", firm: "Vanguard Reach Capital", detail: "Health upgraded to Healthy", ago: "just now" },
];

// How many rows the live feed shows at rest, and how fast a new one lands.
const FEED_VISIBLE = 5;
const FEED_INTERVAL_MS = 3500;

// "ago" labels handed to rows as they age down the stack — index 0 is the
// freshly-landed row, deeper rows read progressively older.
const AGO_LADDER = ["just now", "2m", "8m", "17m", "31m", "48m"];

// ── (A) Horizontal ticker tape ──────────────────────────────────────
// A single marquee line of filing codes. Plain codes (no firm-specific
// detail) so it reads as a fast "wire" scrolling past, distinct from the
// considered vertical feed below.

type TapeItem = { variant: PillVariant; tag: string; code: string; firm: string };

const TAPE_ITEMS: readonly TapeItem[] = [
  { variant: "form", tag: "Form BD", code: "CRD 318204", firm: "Cedar Ridge Securities" },
  { variant: "critical", tag: "17a-11", code: "EW-0042", firm: "Atlas Capital Markets" },
  { variant: "self", tag: "Clearing", code: "X-17A-5", firm: "Brightwater Partners" },
  { variant: "healthy", tag: "FOCUS", code: "FY+38%", firm: "Meridian Trade Group" },
  { variant: "form", tag: "Form BD", code: "CRD 327755", firm: "Northpoint Execution" },
  { variant: "self", tag: "Clearing", code: "X-17A-5", firm: "Sterling Cove Securities" },
  { variant: "warning", tag: "17a-11", code: "EW-0117", firm: "Hollis & Crane" },
  { variant: "healthy", tag: "FOCUS", code: "FY+12%", firm: "Vanguard Reach Capital" },
  { variant: "form", tag: "Form BD", code: "CRD 331902", firm: "Harborline Trading" },
  { variant: "critical", tag: "17a-11", code: "EW-0009", firm: "Kestrel Securities" },
];

// Symmetric edge-fade mask shared by both the tape and the feed viewport.
const EDGE_MASK_X = "linear-gradient(to right, transparent, #000 7%, #000 93%, transparent)";
const EDGE_MASK_Y = "linear-gradient(to bottom, transparent, #000 14%, #000 86%, transparent)";

function TapeItemView({ item, hidden = false }: { item: TapeItem; hidden?: boolean }) {
  return (
    <li aria-hidden={hidden || undefined} className="flex items-center gap-2.5 pr-6 sm:pr-8">
      {/* tiny live dot */}
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent,#6366f1)]" />
      <Pill variant={item.variant}>{item.tag}</Pill>
      <span className="font-mono text-[11px] tracking-[0.04em] text-[var(--text-dim,#475569)]">{item.code}</span>
      <span className="whitespace-nowrap text-[12px] font-medium text-[var(--text,#0f172a)]">{item.firm}</span>
      {/* faint trailing divider */}
      <span aria-hidden className="ml-2 h-3 w-px bg-[var(--border-2,rgba(30,64,175,0.16))]" />
    </li>
  );
}

function FilingTape() {
  return (
    <div
      aria-label="Streaming filing tape"
      className="relative overflow-hidden border-b border-[var(--border,rgba(30,64,175,0.1))] py-3"
      style={{ maskImage: EDGE_MASK_X, WebkitMaskImage: EDGE_MASK_X }}
    >
      {/* One track, two copies back-to-back -> a seamless -50% translate loop.
          The whole strip is decorative (the feed below carries the real,
          accessible content), so the duplicate copy is hidden from assistive
          tech and the visible copy is summarised by the wrapper's aria-label. */}
      <ul className="animate-marquee-left flex w-max items-center">
        {TAPE_ITEMS.map((item) => (
          <TapeItemView key={item.firm} item={item} />
        ))}
        {TAPE_ITEMS.map((item) => (
          <TapeItemView key={`${item.firm}-dup`} item={item} hidden />
        ))}
      </ul>
    </div>
  );
}

// ── (B) Vertical live feed ──────────────────────────────────────────

function FeedRow({ event, fresh, ago }: { event: FeedEvent; fresh: boolean; ago: string }) {
  const Icon = event.icon;
  return (
    <li
      // Only the freshly-landed top row plays the ticker-in entrance; keying
      // the list by id means React mounts just that one node, so the rest of
      // the rows slide down via normal layout without re-animating.
      className={`flex items-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 px-4 py-3 shadow-[var(--shadow-card)] transition-colors duration-300 hover:border-[var(--accent,#6366f1)]/40 ${
        fresh ? "animate-ticker-in" : ""
      }`}
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#6366f1)]">
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Pill variant={event.variant}>{event.tag}</Pill>
          <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">{event.firm}</span>
        </div>
        <p className="mt-0.5 truncate text-[12px] text-[var(--text-dim,#475569)]">{event.detail}</p>
      </div>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--text-dim,#475569)]">{ago}</span>
    </li>
  );
}

export function RedoFilingWire() {
  // Rolling window into FEED_POOL. `cursor` is the pool index of the row
  // currently at the top; the visible stack walks backwards from there so a
  // fresh row appears on top and the oldest drops off the bottom. A
  // monotonically-rising `tick` gives each landing a stable React key so only
  // the new top row remounts (and thus animates).
  const [cursor, setCursor] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Reduced motion => no interval is ever started, so the feed renders as a
    // calm static snapshot. (The CSS also neutralises .animate-ticker-in, but
    // not starting the timer avoids the work entirely.)
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const id = window.setInterval(() => {
      setCursor((c) => (c + 1) % FEED_POOL.length);
      setTick((t) => t + 1);
    }, FEED_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, []);

  // Build the visible stack newest-first. Each slot's `ago` deepens as it ages
  // down the column, so the freshly-landed row always reads "just now".
  const rows = Array.from({ length: FEED_VISIBLE }, (_, slot) => {
    const poolIndex = ((cursor - slot) % FEED_POOL.length + FEED_POOL.length) % FEED_POOL.length;
    return {
      event: FEED_POOL[poolIndex],
      ago: AGO_LADDER[slot] ?? AGO_LADDER[AGO_LADDER.length - 1],
      // Stable key per landing for the top slot so it remounts + animates;
      // lower slots keep a steady key so they just reflow downward.
      key: slot === 0 ? `fresh-${tick}` : FEED_POOL[poolIndex].id,
      fresh: slot === 0 && tick > 0,
    };
  });

  const topEvent = rows[0]?.event;

  return (
    <section
      aria-label="Live filing wire"
      className="relative border-t border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/40 px-6 py-20 backdrop-blur-sm"
    >
      {/* Dark→light seam: a subtle navy wash continues down from the hero into
          the top of this first bright surface, then dissolves. Decorative. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-40 bg-gradient-to-b from-navy/[0.07] via-navy/[0.02] to-transparent"
      />
      {/* Faint accent orb for depth, anchored to the feed side. */}
      <div
        aria-hidden
        className="pointer-events-none absolute right-0 top-1/4 -z-10 h-72 w-72 rounded-full bg-[var(--accent,#6366f1)]/10 blur-3xl"
      />

      {/* (A) Horizontal ticker tape across the top. */}
      <div className="mx-auto max-w-7xl">
        <FilingTape />
      </div>

      {/* (B) Heading block + vertical live feed. */}
      <div className="mx-auto mt-12 grid max-w-7xl items-center gap-10 lg:grid-cols-2">
        <div>
          {/* Label darkened from text-success (#27AE60, 2.44:1 on this near-
              white band — fails AA) to emerald-700 #047857 (~4.9:1, passes AA)
              while staying a "live/positive" green. The small live dot keeps
              the brighter success green — a 1.5px decorative glyph isn't held
              to text contrast. */}
          <span className="inline-flex items-center gap-2 rounded-full border border-success/20 bg-success/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-[#047857]">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" />
            Live feed
          </span>
          <h2 className="mt-6 text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl">
            The market never stops filing.
            <br />
            Neither do we.
          </h2>
          <p className="mt-4 max-w-md text-base leading-relaxed text-[var(--text-dim,#475569)]">
            Form BD registrations, 17a-11 net-capital deficiencies, FOCUS health
            swings, and clearing-partner changes — captured from SEC and FINRA the
            moment they land, and scored by who is worth your next call.
          </p>

          {/* Quiet legend tying the feed's tags back to the regulatory vocabulary. */}
          <div className="mt-8 flex flex-wrap gap-2">
            <Pill variant="form">Form BD</Pill>
            <Pill variant="critical">17a-11</Pill>
            <Pill variant="healthy">FOCUS</Pill>
            <Pill variant="self">Clearing</Pill>
          </div>
        </div>

        {/* Masked viewport so the freshly-landed row fades in at the top edge
            and the dropping row fades out at the bottom. */}
        <div
          className="relative h-[336px] overflow-hidden"
          style={{ maskImage: EDGE_MASK_Y, WebkitMaskImage: EDGE_MASK_Y }}
        >
          <ul className="space-y-3">
            {rows.map((row) => (
              <FeedRow key={row.key} event={row.event} ago={row.ago} fresh={row.fresh} />
            ))}
          </ul>

          {/* Polite, deliberately low-traffic live region — announces only the
              firm + tag of each newly-landed filing, never the whole list, so
              screen readers aren't flooded by the rotation. */}
          <p className="sr-only" aria-live="polite">
            {topEvent ? `New filing: ${topEvent.tag} — ${topEvent.firm}. ${topEvent.detail}.` : ""}
          </p>
        </div>
      </div>
    </section>
  );
}
