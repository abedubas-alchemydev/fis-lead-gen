import type {
  ExecutiveSource,
  UnknownReason,
  UnknownReasonCategory,
} from "@/lib/types";

// Human-readable label for each unknown_reason category. Sourced from the
// cli01 BE contract (feature/be-unknown-reasons-api). Surfaced verbatim in
// the UnknownCell tooltip when a master-list / firm-detail field is null.
export const UNKNOWN_REASON_LABELS: Record<UnknownReasonCategory, string> = {
  firm_does_not_disclose:
    "Firm doesn't disclose this — fully-disclosed exemption",
  no_filing_available: "No recent X-17A-5 filing on SEC EDGAR",
  low_confidence_extraction:
    "Extraction confidence below threshold — pending re-review",
  pdf_unparseable:
    "Filing PDF couldn't be parsed (corrupt or scanned image)",
  provider_error: "Extraction provider returned an error — retry pending",
  not_yet_extracted: "Pipeline hasn't covered this firm yet",
  data_not_present: "Source filing doesn't include this field",
};

export function unknownReasonShort(reason: UnknownReason): string {
  return UNKNOWN_REASON_LABELS[reason.category] ?? "Reason unavailable";
}

// Visual treatment for the source pill that sits next to an executive
// contact's name. Sourced from the cli01 BE contract for
// `feature/be-apollo-executive-enrichment`:
//   - sec    → null (no badge; SEC filing is the authoritative source)
//   - apollo → amber "Enriched" pill
//   - finra  → blue "FINRA officer" pill
//
// `tone` stays as a small string union so this module doesn't have to
// reach into components/ui/pill — the SourceBadge component maps it to
// a PillVariant. The tooltip explains the source distinction so users
// can tell at a glance which names are SEC-authoritative.
export const SOURCE_BADGE: Record<
  ExecutiveSource,
  { label: string; tone: "amber" | "blue"; tooltip: string } | null
> = {
  sec: null,
  apollo: {
    label: "Enriched",
    tone: "amber",
    tooltip:
      "Name from Apollo enrichment, not directly from the firm's SEC filing",
  },
  finra: {
    label: "FINRA officer",
    tone: "blue",
    tooltip: "Name from FINRA executive officers (not from FOCUS report)",
  },
};

// Source pill for the firm-detail website link, fed by the lazy resolver
// in cli01 BE PR feature/be-firm-website-resolver. Tones are deliberately
// scaled by authority:
//   - finra  → blue (FINRA Form BD is the authoritative open-record)
//   - apollo → amber (third-party enrichment, same tone as the executive
//              "Enriched" pill so users learn one signal across surfaces)
//   - hunter → teal (less authoritative than FINRA, higher confidence
//              than open-web search; cyan/teal sits between blue and the
//              warning amber visually)
//
// Kept independent of `SOURCE_BADGE` so the two can evolve independently
// (executive sources include "sec" with no badge; website sources don't).
export type WebsiteSourceTone = "amber" | "blue" | "teal";

export const WEBSITE_SOURCE_BADGE: Record<
  "finra" | "apollo" | "hunter",
  { label: string; tone: WebsiteSourceTone; tooltip: string }
> = {
  finra: {
    label: "FINRA",
    tone: "blue",
    tooltip: "Pulled from FINRA Form BD",
  },
  apollo: {
    label: "Enriched",
    tone: "amber",
    tooltip: "Resolved via Apollo organization data",
  },
  hunter: {
    label: "Hunter",
    tone: "teal",
    tooltip: "Resolved via Hunter company lookup",
  },
};

export function formatCurrency(value: number | null) {
  if (value === null) {
    return "N/A";
  }

  // Compact notation makes the unit visible (e.g. "$74.3M", "$1.5B") so
  // capital values can never be read as bare numbers when a column truncates.
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value);
}

export function formatPercent(value: number | null) {
  if (value === null) {
    return "N/A";
  }

  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function formatDate(value: string | null) {
  if (!value) {
    return "Not available";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(value));
}

export function formatRelativeTime(value: string) {
  const now = Date.now();
  const target = new Date(value).getTime();
  const deltaMs = target - now;

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["day", 1000 * 60 * 60 * 24],
    ["hour", 1000 * 60 * 60],
    ["minute", 1000 * 60]
  ];

  for (const [unit, ms] of units) {
    if (Math.abs(deltaMs) >= ms || unit === "minute") {
      return new Intl.RelativeTimeFormat("en-US", { numeric: "auto" }).format(
        Math.round(deltaMs / ms),
        unit
      );
    }
  }

  return "just now";
}
