// ── DOX Share — trimmed, read-only payload contracts ────────────────────────
//
// Hand-written mirrors of the *allowlisted* fields the public share backend
// sends for each entity. Deliberately NOT derived (Omit<> / Pick<>) from the
// authed response types in @/lib/types: the share wire payloads are trimmed
// server-side (no DB ids, no lead scoring, no favorites, no alerts, no
// extraction / enrichment diagnostics, no filing history), so deriving from
// the authed shapes would silently re-widen this contract whenever those
// types grow a field. Small leaf types that ship unchanged (EmailHit /
// PhoneHit, clearing arrangement rows, bank application events, …) are
// imported so the two surfaces can't drift.
//
// Nullability mirrors the corresponding authed types (most scalars are
// `| null` on the wire).

import type {
  BankApplicationEventItem,
  BankPdfLink,
  BankSourceLink,
  ClearingArrangementItem,
  ClearingMembershipItem,
  DeficiencyStatusSummary,
  EmailHit,
  IndustryArrangementItem,
  IntroducingArrangementItem,
  PhoneHit,
  RegistrationComplianceSummary,
} from "@/lib/types";

// One enriched contact (BD executive contact / bank charter-application
// contact / advisor contact) with the DB id and provenance columns stripped.
export interface PublicContactItem {
  name: string;
  // Bank contact titles are nullable on the wire (BD / IA contacts always
  // carry a string); the shared share shape uses the widest of the three.
  title: string | null;
  // 'contact_person' | 'organizer' | 'proposed_officer' | 'counsel' — bank
  // charter-application rows only; absent on BD / advisor contacts.
  role_context?: string | null;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  emails: EmailHit[];
  phones: PhoneHit[];
}

// One discovered-email row (the email-extractor scan output) trimmed to the
// display columns — enrichment status / verification diagnostics removed.
export interface PublicDiscoveredEmailRow {
  email: string;
  name: string | null;
  title: string | null;
  phone: string | null;
  linkedin_url: string | null;
}

// One FOCUS financial period, trimmed from FinancialMetricItem (no ids, no
// extraction_status / unknown_reason diagnostics).
export interface PublicFinancialRow {
  report_date: string;
  net_capital: number;
  excess_net_capital: number | null;
  total_assets: number | null;
  required_min_capital: number | null;
  source_filing_url: string | null;
}

// One advisor regulatory filing, trimmed from AdvisorFilingItem (no ids, no
// priority / read state). Carried on the payload; the share renderer does
// not surface a filings section.
export interface PublicAdvisorFilingRow {
  form_type: string;
  filed_at: string;
  summary: string;
  source_filing_url: string | null;
}

// FINRA Form BD owner / officer JSONB rows — mirror the inline shapes on
// BrokerDealerListItem (which are anonymous there, so they can't be
// imported).
export interface BdOwnerRecord {
  name: string;
  title: string;
  ownership_pct?: string;
}

export interface BdOfficerRecord {
  name: string;
  title: string;
}

// Form ADV Schedule A/B rows arrive as loose string maps (mirrors
// InvestmentAdvisorListItem.direct_owners / indirect_owners /
// executive_officers).
export type AdvisorOwnerRecord = Record<string, string>;

// ── Broker-dealer ───────────────────────────────────────────────────────────

export interface BdSharePayload {
  name: string;
  crd_number: string | null;
  sec_file_number: string | null;
  cik: string | null;
  city: string | null;
  state: string | null;
  website: string | null;
  status: string;
  registration_date: string | null;
  formation_date: string | null;
  business_type: string | null;
  branch_count: number | null;
  dba_names: string[] | null;
  types_of_business: string[] | null;
  types_of_business_other: string | null;
  types_of_business_total: number | null;
  firm_operations_text: string | null;
  direct_owners: BdOwnerRecord[] | null;
  executive_officers: BdOfficerRecord[] | null;
  member_agencies: string[];
  current_clearing_partner: string | null;
  current_clearing_type: string | null;
  last_filing_date: string | null;
  filings_index_url: string | null;
  latest_net_capital: number | null;
  latest_excess_net_capital: number | null;
  latest_total_assets: number | null;
  required_min_capital: number | null;
  yoy_growth: number | null;
  three_year_cagr: number | null;
  health_status: string | null;
  is_deficient: boolean;
  financials: PublicFinancialRow[];
  clearing_arrangements: ClearingArrangementItem[];
  clearing_memberships: ClearingMembershipItem[];
  introducing_arrangements: IntroducingArrangementItem[];
  industry_arrangements: IndustryArrangementItem[];
  registration_compliance: RegistrationComplianceSummary;
  deficiency_status: DeficiencyStatusSummary;
  executive_contacts: PublicContactItem[];
  discovered_emails: PublicDiscoveredEmailRow[];
}

// ── Bank ────────────────────────────────────────────────────────────────────

export interface BankSharePayload {
  name: string;
  fdic_cert: string | null;
  fed_rssd: string | null;
  occ_charter_number: string | null;
  occ_control_number: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  website: string | null;
  charter_authority: string | null;
  charter_type: string | null;
  charter_status: string;
  bkclass: string | null;
  regulator: string | null;
  lei: string | null;
  established_date: string | null;
  insured_date: string | null;
  application_received_date: string | null;
  last_action_date: string | null;
  // FDIC BankFind ASSET/DEP as reported — $ THOUSANDS, not dollars.
  asset: number | null;
  deposits: number | null;
  offices: number | null;
  active: boolean | null;
  digital_assets: boolean;
  digital_asset_pdfs: BankPdfLink[];
  application_events: BankApplicationEventItem[];
  source_links: BankSourceLink[];
  contacts: PublicContactItem[];
  discovered_emails: PublicDiscoveredEmailRow[];
}

// ── Investment advisor ──────────────────────────────────────────────────────

export interface AdvisorSharePayload {
  name: string;
  legal_name: string | null;
  crd_number: string | null;
  sec_file_number: string | null;
  cik: string | null;
  city: string | null;
  state: string | null;
  status: string;
  registration_date: string | null;
  formation_date: string | null;
  last_filing_date: string | null;
  filings_index_url: string | null;
  website: string | null;
  regulatory_aum: number | null;
  discretionary_aum: number | null;
  non_discretionary_aum: number | null;
  total_clients: number | null;
  advisory_activities: string[] | null;
  client_types: string[] | null;
  client_counts: Record<string, number> | null;
  direct_owners: AdvisorOwnerRecord[] | null;
  indirect_owners: AdvisorOwnerRecord[] | null;
  executive_officers: AdvisorOwnerRecord[] | null;
  // Not in the original allowlist sketch, but the share Overview section
  // renders the firm-operations blurb (mirroring the authed page), so the
  // payload must carry it. Nullable + render-guarded: absent/null simply
  // hides the paragraph.
  firm_operations_text: string | null;
  other_business_names: string[] | null;
  files_13f: boolean;
  latest_13f_filing_date: string | null;
  member_agencies: string[];
  contacts: PublicContactItem[];
  filings: PublicAdvisorFilingRow[];
  discovered_emails: PublicDiscoveredEmailRow[];
}

// ── Renderer prop contracts (imported by the share shell's dispatch) ────────

export interface BdShareProfileProps {
  data: BdSharePayload;
}

export interface BankShareProfileProps {
  data: BankSharePayload;
}

export interface AdvisorShareProfileProps {
  data: AdvisorSharePayload;
}
