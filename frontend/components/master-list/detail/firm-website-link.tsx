"use client";

import { useEffect, useRef, useState } from "react";
import { Globe, Loader2, Search } from "lucide-react";

import { Pill, type PillVariant } from "@/components/ui/pill";
import {
  resolveWebsite,
  type ResolveWebsiteResponse,
} from "@/lib/api";
import { WEBSITE_SOURCE_BADGE, type WebsiteSourceTone } from "@/lib/format";

// Renders the clickable website row directly under the firm-name h1 on
// /master-list/{id}.
//
// Rendering policy (cli02 FE-1 — auto-resolve):
//   1. If `website` is already set on the BD record, render the Globe link
//      directly. No API call, no badge (the persisted column doesn't carry
//      a source label today).
//   2. If `website` is null, render the existing "Search Google for this
//      firm" fallback IMMEDIATELY (no dead spinner) and fire a background
//      POST /broker-dealers/{id}/resolve-website. On a non-null response,
//      swap the fallback for the Globe link + a small source pill (smooth
//      fade). On null/error, keep the Google fallback unchanged.
//   3. If `website` is null but a `fallbackDomain` is supplied (e.g. a domain
//      derived from a FOCUS-extracted filing contact's email — the same
//      domain that lights up the Email Extractor), render it with a muted
//      "From filing" badge ahead of the spinner/empty states. The background
//      resolver still runs and upgrades it to a confirmed website if found.
//
// The mount-fired call is deduped via a ref so StrictMode's dev double-
// invoke can't fire it twice. We only fire once per page mount; a refresh
// is the user's only retry path.
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

function ResolvingSpinner() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)]">
      <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
      Resolving website…
    </span>
  );
}

function NoWebsiteNote() {
  // Shown when the lazy resolver chain (Apollo → Hunter → SerpAPI) ran
  // and produced no valid candidate. Many small broker-dealers don't
  // maintain a public website at all — this is the polite null-state so
  // it's clear the system tried, rather than looking like a missing
  // field. The Google search link still renders alongside as a manual
  // escape hatch.
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] italic"
      title="We checked Hunter and Google. Many small broker-dealers don't publish a public website."
    >
      <Globe className="h-3.5 w-3.5 opacity-60" strokeWidth={2} />
      No public website on file
    </span>
  );
}

export function FirmWebsiteLink({
  firmId,
  firmName,
  website,
  fallbackDomain = null,
}: {
  firmId: number;
  firmName: string;
  website: string | null;
  // Lowest-priority display fallback (e.g. a domain derived from a FOCUS-
  // extracted filing contact's email). Shown only when no website is
  // persisted or resolved; does not suppress the background resolver.
  fallbackDomain?: string | null;
}) {
  const persisted = (website ?? "").trim();
  const fallback = (fallbackDomain ?? "").trim();
  const [resolved, setResolved] = useState<ResolveWebsiteResponse | null>(null);
  // Initial isResolving=true when we'll fire the lazy lookup; this avoids a
  // one-frame flash of the "No public website on file" note before the
  // useEffect tick kicks in and switches us to the spinner.
  const [isResolving, setIsResolving] = useState(!website);
  const [lookupDone, setLookupDone] = useState(false);
  const firedRef = useRef(false);

  useEffect(() => {
    if (persisted || firedRef.current) {
      return;
    }
    firedRef.current = true;
    setIsResolving(true);

    resolveWebsite(firmId)
      .then((r) => {
        if (r.website) {
          setResolved(r);
        }
      })
      .catch(() => {
        // Swallow — Google fallback stays. One call per page mount; the
        // user's only retry path is a manual refresh.
      })
      .finally(() => {
        setIsResolving(false);
        setLookupDone(true);
      });
  }, [firmId, persisted]);

  // Domain side precedence: persisted → resolver hit → filing-derived
  // fallback → resolving spinner → polite "No public website on file" note.
  // The filing fallback sits ahead of the spinner/empty states so a firm with
  // no resolvable website still reflects the domain that lit up the Email
  // Extractor; the background resolver keeps running and upgrades it to a
  // confirmed website if it finds one. The Google fallback always renders
  // alongside as a manual escape hatch.
  let domainSide: JSX.Element | null = null;
  if (persisted) {
    domainSide = <ResolvedLink website={persisted} badge={null} />;
  } else if (resolved?.website) {
    domainSide = (
      <ResolvedLink
        website={resolved.website}
        badge={
          resolved.website_source
            ? WEBSITE_SOURCE_BADGE[resolved.website_source]
            : null
        }
      />
    );
  } else if (fallback) {
    domainSide = <ResolvedLink website={fallback} badge={FILING_BADGE} />;
  } else if (isResolving) {
    domainSide = <ResolvingSpinner />;
  } else if (lookupDone) {
    domainSide = <NoWebsiteNote />;
  }

  // Always render the Google search link alongside (or as fallback when
  // domainSide is null). Layout flexes so the two sit side-by-side on wide
  // screens and stack on narrow ones.
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 transition-opacity duration-200">
      {domainSide}
      <GoogleFallback firmName={firmName} />
    </div>
  );
}
