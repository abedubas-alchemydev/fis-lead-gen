import type { UnknownReason, UnknownReasonCategory } from "@/lib/types";

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
