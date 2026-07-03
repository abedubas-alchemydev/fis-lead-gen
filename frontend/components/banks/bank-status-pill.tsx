import { Pill, type PillVariant } from "@/components/ui/pill";

// Charter-status vocabulary shared by the list filter, the table rows, and
// the detail header. Values mirror the BE lifecycle written by the watcher
// (backend/app/services/occ_cas.py CHARTER_STATUS_RANK): pending →
// approved → opened, with withdrawn/rescinded as terminal exits. Labels use
// the client's wording — "Pending Application" rather than bare "pending".
export const CHARTER_STATUS_OPTIONS = [
  { value: "pending", label: "Pending Application" },
  { value: "approved", label: "Approved" },
  { value: "opened", label: "Opened" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "rescinded", label: "Rescinded" },
] as const;

const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  CHARTER_STATUS_OPTIONS.map((option) => [option.value, option.label]),
);

// Tone scale mirrors the lifecycle: amber while the application is open,
// blue once approved (conditionally — not yet operating), green when the
// bank actually opened, muted/red for the exit states.
const STATUS_VARIANTS: Record<string, PillVariant> = {
  pending: "warning",
  approved: "ok",
  opened: "healthy",
  withdrawn: "unknown",
  rescinded: "risk",
};

export function charterStatusLabel(status: string): string {
  return (
    STATUS_LABELS[status] ??
    // Unknown/legacy value — surface it capitalized rather than hiding it.
    status.charAt(0).toUpperCase() + status.slice(1)
  );
}

export function BankStatusPill({ status }: { status: string }) {
  return (
    <Pill variant={STATUS_VARIANTS[status] ?? "unknown"}>
      {charterStatusLabel(status)}
    </Pill>
  );
}

// charter_authority display helper. The wire carries FDIC's CHRTAGNT
// vocabulary verbatim: 'OCC' for national charters (also stamped on CAS
// applications), 'STATE' for state charters.
export function charterAuthorityLabel(authority: string | null): string {
  if (authority === "OCC") return "National (OCC)";
  if (authority === "STATE") return "State";
  return authority ?? "—";
}

// charter_type display helper — distinct from charter_authority above. The
// wire carries OCC's descriptive CharterType verbatim (e.g. 'National',
// 'TrustCo-National'); it's already human-readable, so we surface it as-is
// and only tidy the one hyphenated trust token. Drives the list's Charter
// Type multi-select labels + its active-filter chips.
export function charterTypeLabel(charterType: string | null): string {
  if (!charterType) return "—";
  if (charterType === "TrustCo-National") return "National Trust Co.";
  return charterType;
}

// Why a bank shows "—" instead of a value. Shared verbatim between the
// list's dash tooltips (bank-list-workspace-client.tsx) and the detail
// page's timeline empty-state (bank-detail-client.tsx) so the two
// surfaces never drift apart.
export const DIGITAL_ASSETS_DASH_EXPLANATION =
  "Not listed on OCC's digital-assets applications registry";
export const NO_OCC_TIMELINE_EXPLANATION =
  "No OCC application timeline — state-chartered banks don't file with the OCC";
