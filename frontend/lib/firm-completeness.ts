import type { BrokerDealerListItem } from "@/lib/types";

// Single source of truth for "is this firm row missing data the user can
// trigger a refresh for?". Used by both the master-list workspace (per-row
// gate for the leftmost RefreshFirmButton column) and the firm-detail page
// (gate for the button next to the firm-name h1).
//
// Mirrors the four gates the BE's POST /broker-dealers/{id}/refresh-all
// endpoint inspects on the BD record:
//   - financial_unknown_reason → financials sub-pipeline
//   - current_clearing_unknown_reason → health-check sub-pipeline
//   - website == null → resolve-website sub-pipeline
// (executive-contacts emptiness is the fourth gate, but exec contacts
// aren't on the master-list row payload, so the FE can't check it here.
// The BE's endpoint still re-runs the contacts sub-pipeline on its own
// gate — the FE just won't show the button on a row that's *only*
// missing contacts. Acceptable trade-off for v1.)
export function isFirmIncomplete(
  item: Pick<
    BrokerDealerListItem,
    | "current_clearing_unknown_reason"
    | "financial_unknown_reason"
    | "website"
  >,
): boolean {
  return (
    item.current_clearing_unknown_reason != null ||
    item.financial_unknown_reason != null ||
    !item.website
  );
}
