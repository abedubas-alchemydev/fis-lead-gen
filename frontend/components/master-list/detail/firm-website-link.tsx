"use client";

import { useEffect, useRef, useState } from "react";
import { Globe, Search } from "lucide-react";

import { Pill, type PillVariant } from "@/components/ui/pill";
import {
  resolveWebsite,
  type ResolveWebsiteResponse,
  type WebsiteSource,
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
  source,
}: {
  website: string;
  source: WebsiteSource | null;
}) {
  const href = website.startsWith("http") ? website : `https://${website}`;
  const display =
    website
      .replace(/^https?:\/\//i, "")
      .replace(/^www\./i, "")
      .replace(/\/+$/, "")
      .split("/")[0]
      ?.toLowerCase() ?? website;

  const badge = source ? WEBSITE_SOURCE_BADGE[source] : null;

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

export function FirmWebsiteLink({
  firmId,
  firmName,
  website,
}: {
  firmId: number;
  firmName: string;
  website: string | null;
}) {
  const persisted = (website ?? "").trim();
  const [resolved, setResolved] = useState<ResolveWebsiteResponse | null>(null);
  const firedRef = useRef(false);

  useEffect(() => {
    if (persisted || firedRef.current) {
      return;
    }
    firedRef.current = true;

    resolveWebsite(firmId)
      .then((r) => {
        if (r.website) {
          setResolved(r);
        }
      })
      .catch(() => {
        // Swallow — Google fallback stays. One call per page mount; the
        // user's only retry path is a manual refresh.
      });
  }, [firmId, persisted]);

  if (persisted) {
    return (
      <div className="mt-1.5">
        <ResolvedLink website={persisted} source={null} />
      </div>
    );
  }

  if (resolved?.website) {
    return (
      <div className="mt-1.5 transition-opacity duration-200">
        <ResolvedLink website={resolved.website} source={resolved.website_source} />
      </div>
    );
  }

  return (
    <div className="mt-1.5">
      <GoogleFallback firmName={firmName} />
    </div>
  );
}
