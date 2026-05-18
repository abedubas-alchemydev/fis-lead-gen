// `unknown_reason` is the BE-provided explanation for why a nullable
// master-list field is missing. Mirrors backend/app/schemas/unknown_reason.py
// from cli01's BE PR (#222 / `feature/be-unknown-reasons-api`).
//
// The BE consolidates per-group: every `BrokerDealerListItem` carries a
// `current_clearing_unknown_reason` (covers partner + type) and a
// `financial_unknown_reason` (covers net capital, excess capital, YoY).
// Per-row sub-models (ClearingArrangementItem, FinancialMetricItem) carry
// their own `unknown_reason`. None ⇒ value is present and the cell renders
// normally. Non-None ⇒ value is missing and the FE renders an info icon.
export type UnknownReasonCategory =
  | "firm_does_not_disclose"
  | "no_filing_available"
  | "low_confidence_extraction"
  | "pdf_unparseable"
  | "provider_error"
  | "not_yet_extracted"
  | "data_not_present";

export type UnknownReason = {
  category: UnknownReasonCategory;
  note: string | null;
  extracted_at: string | null;
  confidence: number | null;
};

export type BrokerDealerListItem = {
  id: number;
  cik: string | null;
  crd_number: string | null;
  sec_file_number: string | null;
  name: string;
  city: string | null;
  state: string | null;
  status: string;
  branch_count: number | null;
  business_type: string | null;
  registration_date: string | null;
  matched_source: string;
  last_filing_date: string | null;
  filings_index_url: string | null;
  required_min_capital: number | null;
  latest_net_capital: number | null;
  latest_excess_net_capital: number | null;
  latest_total_assets: number | null;
  yoy_growth: number | null;
  three_year_cagr: number | null;
  health_status: string | null;
  is_deficient: boolean;
  latest_deficiency_filed_at: string | null;
  // BE boundary fields — names mirror the FastAPI response contract verbatim
  // (broker_dealers.lead_score / lead_priority columns). The FE displays
  // these as "prospect score" / "prospect priority" everywhere user-facing,
  // but the wire shape stays in BE vocabulary. Don't rename here.
  lead_score: number | null;
  lead_priority: string | null;
  current_clearing_partner: string | null;
  current_clearing_type: string | null;
  current_clearing_is_competitor: boolean;
  current_clearing_source_filing_url: string | null;
  current_clearing_extraction_confidence: number | null;
  last_audit_report_date: string | null;
  // BE-derived: populated when ANY field in the clearing cluster
  // (current_clearing_partner, current_clearing_type) is None. One reason
  // covers both the partner and the clearing-type cells in the FE; `note`
  // is prefixed with `[Triggered by missing: <field>]` so the tooltip can
  // name the specific column.
  current_clearing_unknown_reason?: UnknownReason | null;
  // BE-derived: populated when ANY field in the financial-health cluster
  // (latest_net_capital, latest_excess_net_capital, yoy_growth, health_status)
  // is None. One reason covers all four financial tiles (Net Capital /
  // Excess Capital / YoY / Financial Health) in the FE; `note` carries the
  // trigger-field annotation.
  financial_unknown_reason?: UnknownReason | null;
  // Tri-Stream fields (Revision 1)
  website: string | null;
  types_of_business: string[] | null;
  direct_owners: Array<{ name: string; title: string; ownership_pct?: string }> | null;
  executive_officers: Array<{ name: string; title: string }> | null;
  firm_operations_text: string | null;
  clearing_classification: string | null;
  clearing_raw_text: string | null;
  is_niche_restricted: boolean;
  formation_date: string | null;
  total_assets_yoy: number | null;
  types_of_business_total: number | null;
  types_of_business_other: string | null;
  dba_names: string[] | null;
  created_at: string;
};

export type BrokerDealerListResponse = {
  items: BrokerDealerListItem[];
  meta: {
    page: number;
    limit: number;
    total: number;
    total_pages: number;
    // ISO-8601 timestamp of the most recent pipeline_run (completed_at or
    // started_at fallback). Null when no runs have landed yet. Surfaced here
    // so the master-list topbar can render a refresh stamp for all users,
    // not just admins.
    pipeline_refreshed_at: string | null;
  };
};

export type DashboardStats = {
  total_active_bds: number;
  new_bds_30_days: number;
  deficiency_alerts: number;
  // BE boundary field — mirrors FastAPI response shape. Counts firms with
  // latest_net_capital in the [$5M, $100M] band (the "High Value Participant"
  // business rule); decoupled from the ACG ICP scorer's lead_priority.
  high_value_participants: number;
};

export type FinancialMetricItem = {
  id: number;
  bd_id: number;
  report_date: string;
  net_capital: number;
  excess_net_capital: number | null;
  total_assets: number | null;
  required_min_capital: number | null;
  source_filing_url: string | null;
  extraction_status: string;
  created_at: string;
  // Populated when extraction_status != "parsed" (needs_review,
  // provider_error, pipeline_error, missing_pdf). None on parsed rows.
  unknown_reason?: UnknownReason | null;
};

export type FinancialMetricsResponse = {
  items: FinancialMetricItem[];
};

export type AlertListItem = {
  id: number;
  bd_id: number;
  firm_name: string;
  form_type: string;
  priority: string;
  filed_at: string;
  summary: string;
  source_filing_url: string | null;
  is_read: boolean;
};

export type AlertListResponse = {
  items: AlertListItem[];
  meta: {
    page: number;
    limit: number;
    total: number;
    total_pages: number;
  };
};

export type AlertReadResponse = {
  id: number;
  is_read: boolean;
};

// ── Investors tab (SEC Form 4 insider transactions) ───────────────────
// One row per (reporting-person × Form 4 transaction) pair already
// passed through the $50K / last-3-months filter on the BE. ad_code is
// "A" (acquired/buy) or "D" (disposed/sell) — the FE partitions the
// two product-facing lists on that field.
export type InvestorItem = {
  id: number;
  accession_number: string;
  is_derivative: boolean;

  issuer_cik: string;
  issuer_name: string;
  issuer_ticker: string | null;

  reporting_owner_cik: string;
  reporting_owner_name: string;
  reporting_owner_title: string | null;
  reporting_owner_is_director: boolean;
  reporting_owner_is_officer: boolean;
  reporting_owner_is_ten_pct: boolean;
  reporting_owner_street1: string | null;
  reporting_owner_street2: string | null;
  reporting_owner_city: string | null;
  reporting_owner_state: string | null;
  reporting_owner_zip: string | null;

  security_title: string | null;
  transaction_date: string;
  transaction_code: string | null;
  ad_code: "A" | "D";
  shares: number | null;
  price_per_share: number | null;
  transaction_value: number | null;

  enriched_phone: string | null;
  enriched_email: string | null;
  enriched_at: string | null;

  source_filing_url: string | null;
  filed_at: string;
};

export type InvestorListResponse = {
  items: InvestorItem[];
  meta: {
    page: number;
    limit: number;
    total: number;
    total_pages: number;
  };
};

export type InvestorEnrichResponse = {
  item: InvestorItem;
  matched: boolean;
};

export type AlertsBulkReadResponse = {
  updated_count: number;
};

export type FilingMonitorRunResponse = {
  run_id: number;
  total_items: number;
  success_count: number;
  failure_count: number;
  status: string;
};

export type ClearingArrangementItem = {
  id: number;
  bd_id: number;
  filing_year: number;
  report_date: string | null;
  source_filing_url: string | null;
  source_pdf_url: string | null;
  clearing_partner: string | null;
  clearing_type: string | null;
  agreement_date: string | null;
  extraction_confidence: number | null;
  extraction_status: string;
  extraction_notes: string | null;
  is_competitor: boolean;
  is_verified: boolean;
  extracted_at: string | null;
  created_at: string;
  // Populated when clearing_partner is None on this row.
  unknown_reason?: UnknownReason | null;
};

export type ClearingArrangementsResponse = {
  items: ClearingArrangementItem[];
};

export type ClearingProviderShare = {
  provider: string;
  count: number;
  percentage: number;
  is_competitor: boolean;
};

export type ClearingDistributionResponse = {
  items: ClearingProviderShare[];
};

export type TimeSeriesRange = "7D" | "30D" | "90D" | "1Y";

export type TimeSeriesBucket = {
  date: string; // ISO YYYY-MM-DD
  registrations: number;
  alerts: number;
};

export type TimeSeriesResponse = {
  range: TimeSeriesRange;
  buckets: TimeSeriesBucket[];
};

export type FilingHistoryItem = {
  label: string;
  filed_at: string;
  summary: string;
  source_filing_url: string | null;
  priority: string | null;
};

export type FilingHistoryPageResponse = {
  items: FilingHistoryItem[];
  total: number;
  page: number;
  limit: number;
  total_pages: number;
};

// Where the executive_contact record was sourced from. Mirrors the cli01 BE
// contract for `feature/be-apollo-executive-enrichment`:
//   - "sec"    — name pulled directly from the firm's FOCUS / SEC filing
//                (most authoritative; no badge in the UI)
//   - "apollo" — name inferred via Apollo enrichment when FOCUS extraction
//                returned no officers (third-party, less authoritative)
//   - "finra"  — name fell back to the FINRA executive-officers list
export type ExecutiveSource = "sec" | "apollo" | "finra";

export type ExecutiveContactItem = {
  id: number;
  bd_id: number;
  name: string;
  title: string;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  source: ExecutiveSource;
  enriched_at: string;
};

export type RegistrationComplianceSummary = {
  registration_status: string;
  registration_date: string | null;
  sec_file_number: string | null;
  crd_number: string | null;
  branch_count: number | null;
  business_type: string | null;
  filings_index_url: string | null;
};

export type DeficiencyStatusSummary = {
  is_deficient: boolean;
  latest_deficiency_filed_at: string | null;
  message: string;
};

export type IntroducingArrangementItem = {
  id: number;
  bd_id: number;
  statement: string | null;
  business_name: string | null;
  effective_date: string | null;
  description: string | null;
};

export type IndustryArrangementKind = "books_records" | "accounts_funds" | "customer_accounts";

export type IndustryArrangementItem = {
  id: number;
  bd_id: number;
  kind: IndustryArrangementKind;
  has_arrangement: boolean;
  partner_name: string | null;
  partner_crd: string | null;
  partner_address: string | null;
  effective_date: string | null;
  description: string | null;
};

export type BrokerDealerProfileResponse = {
  broker_dealer: BrokerDealerListItem;
  financials: FinancialMetricItem[];
  clearing_arrangements: ClearingArrangementItem[];
  introducing_arrangements: IntroducingArrangementItem[];
  industry_arrangements: IndustryArrangementItem[];
  recent_alerts: AlertListItem[];
  filing_history: FilingHistoryItem[];
  executive_contacts: ExecutiveContactItem[];
  registration_compliance: RegistrationComplianceSummary;
  deficiency_status: DeficiencyStatusSummary;
  // Per-user favorite state, scoped to the calling session. Populated by
  // the backend so the detail page renders the heart in its correct state
  // on the first paint without a second round-trip.
  is_favorited: boolean;
  favorited_at: string | null;
};

export type PipelineRunItem = {
  id: number;
  pipeline_name: string;
  trigger_source: string;
  status: string;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  notes: string | null;
  started_at: string;
  completed_at: string | null;
};

export type PipelineStatusResponse = {
  latest_run: PipelineRunItem | null;
  recent_runs: PipelineRunItem[];
  recent_failures: ClearingArrangementItem[];
};

export type PipelineTriggerResponse = {
  run_id: number;
  status: string;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
};

// Returned by POST /api/v1/pipeline/wipe-bd-data on success. Pairs with
// cli01 BE PR feature/be-pipeline-wipe-bd-data. The BE deletes BD data
// inside an audited transaction and reports back which tables were hit
// + the audit log id so admins can correlate the wipe with the
// follow-on initial_load + populate_all runs that the FE chains.
export type WipeBdDataResponse = {
  affected_tables: string[];
  rows_deleted: number;
  audit_log_id: number;
  wiped_at: string;
};

export type TypeOfBusinessOption = {
  type: string;
  count: number;
};

export type CompetitorProviderItem = {
  id: number;
  name: string;
  aliases: string[];
  priority: number;
  is_active: boolean;
};

export type CompetitorProvidersResponse = {
  items: CompetitorProviderItem[];
};

export type ScoringSettingsItem = {
  id: number;
  settings_key: string;
  net_capital_growth_weight: number;
  clearing_arrangement_weight: number;
  financial_health_weight: number;
  registration_recency_weight: number;
};

export type CompetitorProviderCreate = {
  name: string;
  aliases: string[];
  priority: number;
};

export type CompetitorProviderUpdate = {
  aliases: string[];
  priority: number;
  is_active: boolean;
};

export type ClearingPartnerMergeSuggestionStatus =
  | "pending"
  | "accepted"
  | "rejected";

export type ClearingPartnerMergeSuggestionItem = {
  id: number;
  cluster_signature: string;
  variants: string[];
  suggested_name: string;
  min_score: number;
  status: ClearingPartnerMergeSuggestionStatus;
  accepted_provider_id: number | null;
  created_at: string;
  resolved_at: string | null;
};

export type ClearingPartnerMergeSuggestionList = {
  items: ClearingPartnerMergeSuggestionItem[];
  pending_count: number;
  unmatched_count: number;
};

export type ClearingPartnerClusteringRunResponse = {
  new_pending_count: number;
  total_pending_count: number;
  unmatched_count: number;
};

export type ClearingPartnerMergeSuggestionAccept = {
  canonical_name: string;
  display_name: string | null;
  variants: string[];
  priority: number;
};

export type DataRefreshResponse = {
  filing_monitor_run_id: number;
  clearing_pipeline_run_id: number;
  refreshed_broker_dealers: number;
};

export type FocusCeoExtractionResponse = {
  ceo_name: string | null;
  ceo_title: string | null;
  ceo_phone: string | null;
  ceo_email: string | null;
  net_capital: number | null;
  report_date: string | null;
  source_pdf_url: string | null;
  confidence_score: number;
  extraction_status: string;
  extraction_notes: string | null;
};

// ── Vault folders + Outreach drafts ───────────────────────────────────────
// Mirrors backend/app/schemas/vault.py. Each folder is a named service
// (e.g. "Custody") plus a freeform description AND a permanent
// "instructions" string (Deshorn's 2026-05-04 ask) the AI follows on
// every draft for that service.
export type VaultFolder = {
  id: number;
  name: string;
  description: string;
  outreach_instructions: string;
  created_at: string;
  updated_at: string;
};

export type VaultFolderCreate = {
  name: string;
  description: string;
  outreach_instructions?: string;
};

export type VaultFolderUpdate = {
  name?: string;
  description?: string;
  outreach_instructions?: string;
};

// File rows attached to a folder. The status walks
// extracting -> embedding -> ready (success) or any -> failed (terminal-fail).
// FE polls non-terminal rows on a 2s interval until terminal.
export type VaultFolderFileStatus =
  | "extracting"
  | "embedding"
  | "ready"
  | "failed";

export type VaultFolderFile = {
  id: number;
  folder_id: number;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  processing_status: VaultFolderFileStatus;
  processing_error: string | null;
  processing_started_at: string;
  processing_finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type OutreachDraftRequest = {
  broker_dealer_id: number;
  contact_id: number;
  folder_id: number;
};

export type OutreachDraft = {
  subject: string;
  body: string;
};

export type OutreachSendRequest = {
  broker_dealer_id: number;
  contact_id: number;
  folder_id: number;
  subject: string;
  body: string;
};

export type OutreachSendResponse = {
  id: number;
  gmail_message_id: string;
  sent_at: string;
  status: string;
};

// Per-user "sent outreach" history. Body is omitted from list payload to
// keep response sizes down — fetch via OutreachSendDetail when the user
// expands a row. folder_id/folder_name are nullable because folder
// deletion sets ON DELETE SET NULL on the audit row.
export type OutreachSendStatus = "sent" | "failed";

export type OutreachSendItem = {
  id: number;
  sent_at: string;
  status: string;
  subject: string;
  gmail_message_id: string | null;
  error: string | null;
  broker_dealer_id: number;
  broker_dealer_name: string;
  contact_id: number;
  contact_name: string;
  contact_email: string | null;
  folder_id: number | null;
  folder_name: string | null;
  // Populated only when the admin "all users" scope is requested. Null
  // on the per-user (default) scope.
  user_id?: string | null;
  sender_name?: string | null;
  sender_email?: string | null;
};

export type OutreachSendsScope = "mine" | "all";

export type OutreachSendsListResponse = {
  items: OutreachSendItem[];
  total: number;
  limit: number;
  offset: number;
};

export type OutreachSendDetail = OutreachSendItem & {
  body: string;
};

// ── Admin per-user views (admin-only consumers) ──
//
// Mirrors backend/app/schemas/users_admin.py. The saved-firms payload
// flattens both polymorphic item types — broker-dealers and investment
// advisors — into one stream with an `item_type` discriminator so the
// admin table renders a single sortable list. `lists` is the unfiltered
// summary used by the filter-pill row.

export type AdminUserBrief = {
  id: string;
  email: string;
  name: string;
};

export type AdminSavedFirmListSummary = {
  id: number;
  name: string;
  is_default: boolean;
  item_count: number;
};

export type AdminSavedFirmRow = {
  item_type: "broker_dealer" | "advisor";
  target_id: number;
  target_name: string;
  list_id: number;
  list_name: string;
  list_is_default: boolean;
  saved_at: string;
};

export type AdminUserSavedFirmsResponse = {
  items: AdminSavedFirmRow[];
  total: number;
  limit: number;
  offset: number;
  lists: AdminSavedFirmListSummary[];
  user: AdminUserBrief;
};

// ── Investment Advisor (Form ADV / 13F filer) types ──
//
// Mirrors backend/app/schemas/investment_advisor.py. Lives alongside the
// BD types so a single import surfaces both worlds — the advisor list is
// a sibling workspace, not a fork. Field names match the FastAPI response
// contract verbatim so the FE never touches a snake_case-to-camelCase
// adapter for these.

export type InvestmentAdvisorListItem = {
  id: number;
  cik: string | null;
  crd_number: string | null;
  sec_file_number: string | null;
  name: string;
  legal_name: string | null;
  city: string | null;
  state: string | null;
  status: string;
  matched_source: string;
  registration_date: string | null;
  formation_date: string | null;
  last_filing_date: string | null;
  filings_index_url: string | null;
  website: string | null;
  // 'iapd' | 'apollo' | 'serper' | 'serpapi' | null
  website_source: string | null;
  // Form ADV Item 5.F — regulatory AUM (analog of BD latest_net_capital)
  // and the discretionary/non-discretionary split.
  regulatory_aum: number | null;
  discretionary_aum: number | null;
  non_discretionary_aum: number | null;
  total_clients: number | null;
  // Item 5.G checkboxes (analog of types_of_business). Empty array ⇒
  // pipeline hasn't extracted yet; null ⇒ never seen.
  advisory_activities: string[] | null;
  // Item 5.D categories.
  client_types: string[] | null;
  // Item 5.D.3 number-of-clients-per-category, e.g.
  // {"high_net_worth": 12, "individuals": 238}.
  client_counts: Record<string, number> | null;
  // Schedule A direct owners and Schedule B indirect owners.
  direct_owners: Array<Record<string, string>> | null;
  indirect_owners: Array<Record<string, string>> | null;
  executive_officers: Array<Record<string, string>> | null;
  firm_operations_text: string | null;
  // Hard-scope filter flag — true iff EDGAR shows ≥1 Form 13F-HR for
  // this firm in the last 4 quarters. Refreshed by the daily 13F monitor.
  files_13f: boolean;
  latest_13f_filing_date: string | null;
  last_enrich_attempt_at: string | null;
  created_at: string;
  updated_at: string;
};

export type InvestmentAdvisorDetail = InvestmentAdvisorListItem;

export type InvestmentAdvisorListMeta = {
  page: number;
  limit: number;
  total: number;
  total_pages: number;
  pipeline_refreshed_at: string | null;
};

export type InvestmentAdvisorListResponse = {
  items: InvestmentAdvisorListItem[];
  meta: InvestmentAdvisorListMeta;
};

export type AdvisorContactItem = {
  id: number;
  advisor_id: number;
  name: string;
  title: string;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  source: string;
  discovery_source: string | null;
  discovery_confidence: number | null;
  enriched_at: string;
};

export type AdvisorFilingItem = {
  id: number;
  advisor_id: number;
  form_type: string;
  priority: string;
  filed_at: string;
  summary: string;
  source_filing_url: string | null;
  is_read: boolean;
};

export type InvestmentAdvisorProfileResponse = {
  advisor: InvestmentAdvisorDetail;
  contacts: AdvisorContactItem[];
  filings: AdvisorFilingItem[];
  is_favorited: boolean;
};

export type AdvisoryActivityCount = {
  type: string;
  count: number;
};

export type ClientTypeCount = {
  type: string;
  count: number;
};

// ── Institutional Investor (13F filer) types ──
//
// Mirrors backend/app/schemas/institutional_investor.py. ``advisor_id``
// is populated when the same CIK also appears as a registered RIA in
// ``investment_advisors`` -- the FE renders a "View as Investment
// Advisor ->" cross-link in that case.

export type InstitutionalInvestorListItem = {
  id: number;
  cik: string | null;
  advisor_id: number | null;
  name: string;
  legal_name: string | null;
  city: string | null;
  state: string | null;
  status: string;
  matched_source: string;
  website: string | null;
  website_source: string | null;
  latest_13f_filing_date: string | null;
  total_aum: number | null;
  holdings_count: number | null;
  filings_index_url: string | null;
  last_enrich_attempt_at: string | null;
  created_at: string;
  updated_at: string;
};

export type InstitutionalInvestorDetail = InstitutionalInvestorListItem;

export type InstitutionalInvestorListMeta = {
  page: number;
  limit: number;
  total: number;
  total_pages: number;
  pipeline_refreshed_at: string | null;
};

export type InstitutionalInvestorListResponse = {
  items: InstitutionalInvestorListItem[];
  meta: InstitutionalInvestorListMeta;
};

export type InvestorContactItem = {
  id: number;
  investor_id: number;
  name: string;
  title: string;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  source: string;
  discovery_source: string | null;
  discovery_confidence: number | null;
  enriched_at: string;
};

export type InvestorFilingItem = {
  id: number;
  investor_id: number;
  form_type: string;
  priority: string;
  filed_at: string;
  summary: string;
  source_filing_url: string | null;
  is_read: boolean;
};

export type InstitutionalInvestorProfileResponse = {
  investor: InstitutionalInvestorDetail;
  contacts: InvestorContactItem[];
  filings: InvestorFilingItem[];
  is_favorited: boolean;
};

export type AdjacentResponse = {
  prev_id: number | null;
  next_id: number | null;
};

// ── Cross-entity contact search ──
//
// Mirrors backend/app/schemas/contact_search.py. Hits surface a
// ``firm_type`` discriminator (or null when no firm context, e.g. a
// raw discovered_email row not yet attributed to a firm) so the FE
// can render a deep-link to the right detail page.

export type ContactSearchSource =
  | "executive_contact"
  | "advisor_contact"
  | "investor_contact"
  | "discovered_email"
  | "apollo";

export type ContactSearchFirmType =
  | "broker_dealer"
  | "advisor"
  | "institutional_investor";

export type ContactSearchHit = {
  source: ContactSearchSource;
  firm_type: ContactSearchFirmType | null;
  firm_id: number | null;
  firm_name: string | null;
  contact_id: number | null;
  name: string | null;
  title: string | null;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
};

export type ContactSearchResponse = {
  hits: ContactSearchHit[];
  count: number;
};

// ── Polymorphic outreach ──
//
// Advisor/investor draft + send requests. Same shape as the BD variants
// but keyed on the right firm + contact IDs.

export type OutreachAdvisorDraftRequest = {
  advisor_id: number;
  advisor_contact_id: number;
  folder_id: number;
};

export type OutreachAdvisorSendRequest = OutreachAdvisorDraftRequest & {
  subject: string;
  body: string;
};

export type OutreachInvestorDraftRequest = {
  institutional_investor_id: number;
  investor_contact_id: number;
  folder_id: number;
};

export type OutreachInvestorSendRequest = OutreachInvestorDraftRequest & {
  subject: string;
  body: string;
};
