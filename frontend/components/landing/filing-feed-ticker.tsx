"use client";

import { ArrowLeftRight, FileText, ShieldAlert, TrendingUp } from "lucide-react";

import { Pill } from "@/components/ui/pill";
import type { PillVariant } from "@/components/ui/pill";

type FeedEvent = {
  id: string;
  icon: typeof FileText;
  variant: PillVariant;
  tag: string;
  firm: string;
  detail: string;
  ago: string;
};

// Local mock filing stream — no fetch, purely illustrative of the kinds of
// events the platform surfaces. Hardcoded per the frontend-only brief.
const FEED_EVENTS: FeedEvent[] = [
  { id: "f1", icon: FileText, variant: "form", tag: "Form BD", firm: "Cedar Ridge Securities LLC", detail: "New broker-dealer registered", ago: "2m" },
  { id: "f2", icon: ShieldAlert, variant: "critical", tag: "17a-11", firm: "Atlas Capital Markets", detail: "Net-capital deficiency notice", ago: "11m" },
  { id: "f3", icon: ArrowLeftRight, variant: "self", tag: "Clearing", firm: "Brightwater Partners", detail: "Apex → Pershing", ago: "24m" },
  { id: "f4", icon: TrendingUp, variant: "healthy", tag: "FOCUS", firm: "Meridian Trade Group", detail: "Net capital +38% YoY", ago: "41m" },
  { id: "f5", icon: FileText, variant: "form", tag: "Form BD", firm: "Northpoint Execution LLC", detail: "New broker-dealer registered", ago: "1h" },
  { id: "f6", icon: ArrowLeftRight, variant: "self", tag: "Clearing", firm: "Sterling Cove Securities", detail: "Self-clearing → RBC", ago: "1h" },
  { id: "f7", icon: ShieldAlert, variant: "warning", tag: "17a-11", firm: "Hollis & Crane LLC", detail: "Early-warning threshold hit", ago: "2h" },
  { id: "f8", icon: TrendingUp, variant: "healthy", tag: "FOCUS", firm: "Vanguard Reach Capital", detail: "Health upgraded to Healthy", ago: "3h" },
];

function FeedRow({ event, hidden = false }: { event: FeedEvent; hidden?: boolean }) {
  const Icon = event.icon;
  return (
    <li
      aria-hidden={hidden || undefined}
      className="flex items-center gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/70 px-4 py-3"
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#6366f1)]">
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Pill variant={event.variant}>{event.tag}</Pill>
          <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">{event.firm}</span>
        </div>
        <p className="mt-0.5 truncate text-[12px] text-[var(--text-muted,#94a3b8)]">{event.detail}</p>
      </div>
      <span className="shrink-0 text-[11px] tabular-nums text-[var(--text-muted,#94a3b8)]">{event.ago}</span>
    </li>
  );
}

// Vertical auto-scrolling ticker of mock filing events. The list is rendered
// twice back-to-back; `.animate-marquee` translates the track up by 50% for a
// seamless loop and pauses on hover (both defined in globals.css inside the
// reduced-motion guard, so the column sits still under reduced motion). The
// duplicate copy is `aria-hidden` and the whole strip is a labelled, purely
// decorative region — screen readers get one clean pass over the events.
export function FilingFeedTicker() {
  return (
    <section
      aria-label="Live filing feed"
      className="relative border-y border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/40 py-16 backdrop-blur-sm"
    >
      <div className="mx-auto grid max-w-7xl items-center gap-10 px-6 lg:grid-cols-2">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-success/20 bg-success/5 px-4 py-1.5 text-xs font-semibold uppercase tracking-[0.2em] text-success">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-success" />
            Live feed
          </span>
          <h2 className="mt-6 text-3xl font-bold tracking-tight text-[var(--text,#0f172a)] sm:text-4xl">
            The market never stops filing.
            <br />
            Neither do we.
          </h2>
          <p className="mt-4 max-w-md text-base text-[var(--text-muted,#94a3b8)]">
            Form BD registrations, 17a-11 deficiencies, FOCUS health swings, and
            clearing-partner changes — captured from SEC and FINRA and scored as
            they land.
          </p>
        </div>

        {/* Masked viewport so rows fade in/out at the top and bottom edges. */}
        <div
          className="relative h-[336px] overflow-hidden"
          style={{
            maskImage: "linear-gradient(to bottom, transparent, #000 12%, #000 88%, transparent)",
            WebkitMaskImage: "linear-gradient(to bottom, transparent, #000 12%, #000 88%, transparent)",
          }}
        >
          <ul className="animate-marquee space-y-3">
            {FEED_EVENTS.map((event) => (
              <FeedRow key={event.id} event={event} />
            ))}
            {/* Seamless-loop duplicate — hidden from assistive tech. */}
            {FEED_EVENTS.map((event) => (
              <FeedRow key={`${event.id}-dup`} event={event} hidden />
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
