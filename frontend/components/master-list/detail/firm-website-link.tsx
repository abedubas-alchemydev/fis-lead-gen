"use client";

import { Globe, Search } from "lucide-react";

import { Pill, type PillVariant } from "@/components/ui/pill";
import { type WebsiteSourceTone } from "@/lib/format";

// Renders the clickable website row directly under the firm-name h1 on
// /master-list/{id}.
//
// Rendering policy (pure DB read — no paid work on mount):
//   1. If `website` is already set on the BD record, render the Globe link
//      directly. No API call, no badge (the persisted column doesn't carry
//      a source label today).
//   2. If `website` is null but a `fallbackDomain` is supplied (e.g. a domain
//      derived from a FOCUS-extracted filing contact's email — the same
//      domain that lights up the Email Extractor), render it with a muted
//      "From filing" badge.
//   3. Otherwise render the polite "No public website on file" note alongside
//      a "Search Google for this firm" escape hatch.
//
// This component used to fire POST /broker-dealers/{id}/resolve-website on
// mount whenever `website` was null, re-running an Apollo → Hunter → SerpAPI
// waterfall on every visit of a website-less firm. That auto-resolve is
// removed: website resolution now happens only on demand (the firm-detail
// Refresh button's refresh-all includes the resolve-website leg) and via the
// background backfill jobs.
const TONE_TO_VARIANT: Record<WebsiteSourceTone, PillVariant> = {
  amber: "warning",
  blue: "info",
  // Pill ships an `omni` cyan/teal variant that visually matches the
  // "less authoritative than FINRA, more than open-web" position the
  // hunter source occupies. Reusing it avoids a one-off variant addition
  // in components/ui/pill.tsx.
  teal: "omni",
  // Pill `unknown` variant is muted slate — visually signals the
  // "loosest validation tier" that serpapi (web search) occupies.
  gray: "unknown",
};

// Shape of a WEBSITE_SOURCE_BADGE entry, reused for the synthetic
// filing-derived badge below.
type DomainBadge = { label: string; tone: WebsiteSourceTone; tooltip: string };

// Lowest-confidence provenance: the domain was inferred from the email of a
// contact extracted from this firm's SEC filing (e.g. via "Extract FOCUS
// Data"), not from a confirmed website lookup. Muted slate signals the
// loosest validation tier.
const FILING_BADGE: DomainBadge = {
  label: "From filing",
  tone: "gray",
  tooltip:
    "Inferred from the email domain of a contact found in this firm's SEC filing — not a verified website.",
};

function GoogleFallback({ firmName }: { firmName: string }) {
  const googleHref = `https://www.google.com/search?q=${encodeURIComponent(`${firmName} broker-dealer`)}`;
  return (
    <a
      href={googleHref}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)] hover:underline"
    >
      <Search className="h-3.5 w-3.5" strokeWidth={2} />
      Search Google for this firm
    </a>
  );
}

function ResolvedLink({
  website,
  badge,
}: {
  website: string;
  badge: DomainBadge | null;
}) {
  const href = website.startsWith("http") ? website : `https://${website}`;
  const display =
    website
      .replace(/^https?:\/\//i, "")
      .replace(/^www\./i, "")
      .replace(/\/+$/, "")
      .split("/")[0]
      ?.toLowerCase() ?? website;

  return (
    <span className="inline-flex items-center gap-2">
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-[13px] text-[var(--accent,#6366f1)] transition hover:underline"
      >
        <Globe className="h-3.5 w-3.5" strokeWidth={2} />
        {display}
      </a>
      {badge && (
        <span title={badge.tooltip} className="inline-flex">
          <Pill variant={TONE_TO_VARIANT[badge.tone]}>{badge.label}</Pill>
        </span>
      )}
    </span>
  );
}

function NoWebsiteNote() {
  // Shown when no website is persisted (and no filing-derived fallback).
  // Many small broker-dealers don't maintain a public website at all — this
  // is the polite null-state rather than a bare missing field. Resolution is
  // on-demand now (the firm-detail Refresh button) plus the background
  // backfill jobs; the Google search link renders alongside as a manual
  // escape hatch.
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] italic"
      title="No website on file. Use Refresh to look one up, or search Google. Many small broker-dealers don't publish a public website."
    >
      <Globe className="h-3.5 w-3.5 opacity-60" strokeWidth={2} />
      No public website on file
    </span>
  );
}

export function FirmWebsiteLink({
  firmName,
  website,
  fallbackDomain = null,
}: {
  firmName: string;
  website: string | null;
  // Lowest-priority display fallback (e.g. a domain derived from a FOCUS-
  // extracted filing contact's email). Shown only when no website is
  // persisted.
  fallbackDomain?: string | null;
}) {
  const persisted = (website ?? "").trim();
  const fallback = (fallbackDomain ?? "").trim();

  // Domain side precedence: persisted → filing-derived fallback → polite
  // "No public website on file" note. No lookup fires on mount — a
  // website-less firm shows its current empty state, and resolution happens
  // on demand (the firm-detail Refresh button) or via background backfill.
  // The Google fallback always renders alongside as a manual escape hatch.
  let domainSide: JSX.Element | null = null;
  if (persisted) {
    domainSide = <ResolvedLink website={persisted} badge={null} />;
  } else if (fallback) {
    domainSide = <ResolvedLink website={fallback} badge={FILING_BADGE} />;
  } else {
    domainSide = <NoWebsiteNote />;
  }

  // Always render the Google search link alongside. Layout flexes so the two
  // sit side-by-side on wide screens and stack on narrow ones.
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 transition-opacity duration-200">
      {domainSide}
      <GoogleFallback firmName={firmName} />
    </div>
  );
}
