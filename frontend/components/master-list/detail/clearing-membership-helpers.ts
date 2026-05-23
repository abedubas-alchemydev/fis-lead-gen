import type { PillVariant } from "@/components/ui/pill";

// Clearing-agency / SRO membership label helpers, shared by the BD + IA
// list views and detail pages. Co-located under detail/ alongside
// pill-helpers.ts. The agency set is fixed (mirrors the backend
// CLEARING_AGENCIES constant + the importer's seed files), so it is a
// static constant rather than something fetched from the API. Pure
// constant module — safe to import from both the shared list clients and
// the detail clients.

export const CLEARING_AGENCIES = ["OCC", "DTC", "NSCC", "FICC-GOV", "FICC-MBS"] as const;

export type ClearingAgency = (typeof CLEARING_AGENCIES)[number];

const AGENCY_LABELS: Record<string, string> = {
  OCC: "OCC",
  DTC: "DTC",
  NSCC: "NSCC",
  "FICC-GOV": "FICC Gov",
  "FICC-MBS": "FICC MBS",
};

export function agencyLabel(code: string): string {
  return AGENCY_LABELS[code] ?? code;
}

// All agencies share the affirmative "member" variant; the agency name is
// the label. Membership is binary per agency, so colour-by-membership reads
// cleaner than colour-by-agency.
export function membershipVariant(): PillVariant {
  return "member";
}
