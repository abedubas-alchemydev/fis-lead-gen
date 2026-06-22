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
  // Clearing-agency / SRO membership labels. `member_agencies` is the set of
  // agency codes (OCC/DTC/NSCC/FICC-GOV/FICC-MBS) the firm actively belongs
  // to. `clearing_membership_checked_at` is the sentinel: null ⇒ never
  // evaluated (render nothing); non-null + empty `member_agencies` ⇒ "Not a
  // member".
  member_agencies: string[];
  clearing_membership_checked_at: string | null;
};

// One clearing-agency / SRO membership with provenance (firm detail page).
export type ClearingMembershipItem = {
  agency: string;
  member_number: string | null;
  member_name_raw: string;
  source_file: string;
  source_version: string | null;
  match_method: string;
  match_confidence: number | null;
  status: string;
};

// Admin review-queue row: one needs_review candidate joined to its firm.
export type ClearingMembershipReviewRow = {
  id: number;
  agency: string;
  member_number: string | null;
  member_name_raw: string;
  source_file: string;
  source_version: string | null;
  match_method: string;
  match_confidence: number | null;
  firm_side: "broker_dealer" | "investment_advisor";
  firm_id: number;
  firm_name: string;
  created_at: string;
};

export type ClearingMembershipReviewListResponse = {
  items: ClearingMembershipReviewRow[];
  total: number;
};

export type ClearingMembershipDecisionResponse = {
  id: number;
  status: string;
  match_method: string;
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
  new_bds_90_days: number;
  deficiency_alerts: number;
  // BE boundary field — mirrors FastAPI response shape. Counts firms in the
  // "High Value Participant" segment: latest_net_capital in the [$5M, $100M]
  // band OR the OTC corporate-equity retailing business type. Decoupled from
  // the ACG ICP scorer's lead_priority.
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
  txn_count: number;

  enriched_phone: string | null;
  enriched_email: string | null;
  enriched_linkedin_url: string | null;
  enriched_at: string | null;
  // True when an Apollo phone-reveal was requested but the number hasn't
  // landed via the async webhook yet. Drives the "Phone arriving…" hint;
  // always false once a phone is present or no reveal was ever requested.
  phone_pending: boolean;

  source_filing_url: string | null;
  filed_at: string;

  // Favorites (insider). ``reporting_owner_id`` is the surrogate id of the
  // insider's reporting_owners row, or null until first favorited (the
  // heart then adds by ``reporting_owner_cik``). ``is_favorited`` reflects
  // membership in the caller's default list.
  reporting_owner_id: number | null;
  is_favorited: boolean;

  // True when the reporting owner's name looks like an entity (LLC / LP /
  // GP / Fund / Holdings / ...) rather than a natural person — Apollo and
  // PDL only enrich people, so the Enrich button is disabled for these.
  is_entity: boolean;
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
  txn_id: number;
  enriched_phone: string | null;
  enriched_email: string | null;
  enriched_linkedin_url: string | null;
  // NULL when ``skip_reason`` is set (the lookup didn't actually run);
  // ISO timestamp on a real attempt regardless of match outcome.
  enriched_at: string | null;
  matched: boolean;
  // Non-null only for deliberate short-circuits. Today the only value is
  // "entity_filer" — name looks like an org so we never hit Apollo/PDL.
  skip_reason: string | null;
  // True when Apollo returned a record + a reveal was requested but no
  // number came back in the sync body — merged into the row so the
  // "Phone arriving…" hint shows immediately after a click.
  phone_pending: boolean;
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

export type EmailHit = {
  value: string;
  type: "work" | "personal";
  confidence: number | null;
  source: string;
};

export type PhoneHit = {
  value: string;
  type: "mobile" | "work" | "hq";
  confidence: number | null;
  source: string;
};

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
  emails: EmailHit[];
  phones: PhoneHit[];
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
  // Full clearing-agency / SRO membership rows with provenance (active +
  // needs_review).
  clearing_memberships: ClearingMembershipItem[];
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
  // PK of the ``account`` row this folder defaults to when the user
  // opens the Outreach modal for any of this folder's contacts. Null
  // means "no default — apply the user-level fallback chain instead."
  default_sender_account_id: string | null;
  created_at: string;
  updated_at: string;
};

export type VaultFolderCreate = {
  name: string;
  description: string;
  outreach_instructions?: string;
  default_sender_account_id?: string | null;
};

export type VaultFolderUpdate = {
  name?: string;
  description?: string;
  outreach_instructions?: string;
  // ``null`` clears the default, a string sets it. Omit to leave alone
  // (the FE forms use react-state -> explicit-null to clear).
  default_sender_account_id?: string | null;
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

export type OptimizeInstructionsRequest = {
  text: string;
};

export type OptimizeInstructionsResponse = {
  optimized_text: string;
};

export type OutreachSendRequest = {
  broker_dealer_id: number;
  contact_id: number;
  folder_id: number;
  subject: string;
  body: string;
  // Legacy field kept for back-compat. Once ``sender_account_id`` is
  // set the server derives the provider from the account row.
  provider?: EmailProviderId;
  // PK of the ``account`` row to send from. Optional; server applies
  // the 3-tier fallback (folder default -> first send-scoped -> first
  // linked) when omitted.
  sender_account_id?: string | null;
};

export type OutreachSendResponse = {
  id: number;
  gmail_message_id: string;
  sent_at: string;
  status: string;
};

// Per-user outreach signature (footer). `signature` is "" when unset, so
// callers can treat "no footer" and "empty footer" the same. Read by the
// compose surfaces to prefill the editable Footer field and by the
// account-settings editor; written by the editor.
export type OutreachSignature = {
  signature: string;
};

// Per-user "sent outreach" history. Body is omitted from list payload to
// keep response sizes down — fetch via OutreachSendDetail when the user
// expands a row. folder_id/folder_name are nullable because folder
// deletion sets ON DELETE SET NULL on the audit row.
export type OutreachSendStatus = "sent" | "failed";

// Per-user send transport. Drives the provider picker on the Outreach
// modal and the per-row provider badge on the Sent Outreach view.
// Apple is intentionally absent — iCloud SMTP is app-specific-password
// only (no OAuth), which breaks the refresh-token flow.
export type EmailProviderId = "google" | "microsoft" | "yahoo";

export type OutreachSendItem = {
  id: number;
  sent_at: string;
  status: string;
  subject: string;
  // Which transport ran this send. Backfilled to "google" on the
  // migration 0049 upgrade, so pre-PR-C rows are all google.
  provider: EmailProviderId;
  gmail_message_id: string | null;
  error: string | null;
  // Polymorphic across firm types. firm_type discriminates which
  // (id, name) pair is populated:
  //   - "broker_dealer"  -> broker_dealer_id / broker_dealer_name
  //   - "advisor"        -> advisor_id / advisor_name
  //   - "institutional_investor" -> institutional_investor_id /
  //                                  institutional_investor_name
  //   - "adhoc"          -> all firm fields null; recipient_email is
  //                          the actual destination address
  firm_type?: "broker_dealer" | "advisor" | "institutional_investor" | "adhoc";
  broker_dealer_id: number | null;
  broker_dealer_name: string | null;
  advisor_id?: number | null;
  advisor_name?: string | null;
  institutional_investor_id?: number | null;
  institutional_investor_name?: string | null;
  contact_type?:
    | "executive_contact"
    | "advisor_contact"
    | "investor_contact"
    | "adhoc";
  contact_id: number | null;
  advisor_contact_id?: number | null;
  investor_contact_id?: number | null;
  contact_name: string;
  contact_email: string | null;
  // Adhoc destination: populated only on firm_type="adhoc" rows.
  // Contact-based rows leave these null and the FE falls back to
  // contact_email / contact_name.
  recipient_email?: string | null;
  recipient_name?: string | null;
  // Multi-recipient compose-send (POST /outreach/compose-send) audit:
  // comma-joined address lists. Null on single-recipient / contact rows.
  // to_emails is set only when there was more than one To.
  to_emails?: string | null;
  cc_emails?: string | null;
  bcc_emails?: string | null;
  folder_id: number | null;
  folder_name: string | null;
  // Populated only when the admin "all users" scope is requested. Null
  // on the per-user (default) scope.
  user_id?: string | null;
  sender_name?: string | null;
  sender_email?: string | null;
};

// ── /outreach/sent?tab=create surface ───────────────────────────────
// The new Create tab lets the user start a send from scratch. Picks a
// recipient via autocomplete (search across all 3 contact tables) OR
// types a free-form email; the latter posts to /outreach/adhoc-send,
// the former dispatches to the matching per-entity endpoint.

export type RecipientSearchResult = {
  entity_kind: "broker_dealer" | "advisor" | "institutional_investor";
  entity_id: number;
  entity_name: string;
  contact_id: number;
  contact_name: string;
  contact_title: string | null;
  contact_email: string;
};

export type RecipientSearchResponse = {
  items: RecipientSearchResult[];
};

// Firm-hit row in the recipient autocomplete. Picking one opens the
// firm-contacts modal (see FirmContactsResponse). contact_count only
// counts contacts with an email -- a firm whose contacts all lack
// emails can't be sent to and isn't returned by the backend.
export type FirmSearchResult = {
  entity_kind: "broker_dealer" | "advisor" | "institutional_investor";
  entity_id: number;
  entity_name: string;
  contact_count: number;
};

export type FirmSearchResponse = {
  items: FirmSearchResult[];
};

// Email-bearing contacts at one firm. Drives the firm-contacts modal
// opened when the user picks a firm row in the autocomplete (or after
// picking a firm in the favorites drill-down).
export type FirmContactsResponse = {
  entity_kind: "broker_dealer" | "advisor" | "institutional_investor";
  entity_id: number;
  entity_name: string;
  items: RecipientSearchResult[];
};

// Favorite-list row in the recipient autocomplete. Picking one opens
// the favorite-firms modal (drill-down 1: firms in the list). Picking
// a firm in that modal swaps to the firm-contacts view (drill-down 2).
export type FavoriteSearchResult = {
  list_id: number;
  name: string;
  firm_count: number;
};

export type FavoriteSearchResponse = {
  items: FavoriteSearchResult[];
};

export type FavoriteFirmsResponse = {
  list_id: number;
  name: string;
  items: FirmSearchResult[];
};

export type OutreachAdhocSendRequest = {
  recipient_email: string;
  recipient_name?: string | null;
  subject: string;
  body: string;
  sender_account_id?: string | null;
  folder_id?: number | null;
};

// One visible To recipient on a compose-send: address + optional name.
export type OutreachComposeRecipient = {
  email: string;
  name?: string | null;
};

// POST /api/v1/outreach/compose-send — one email to a To/Cc/Bcc set, like
// a normal mail client. To & Cc are visible to each other; Bcc is hidden.
// The server de-dupes addresses across the three buckets (To > Cc > Bcc).
// folder_id is optional service metadata (no folder required to send).
export type OutreachComposeSendRequest = {
  to: OutreachComposeRecipient[];
  cc: string[];
  bcc: string[];
  subject: string;
  body: string;
  sender_account_id?: string | null;
  folder_id?: number | null;
};

// POST /api/v1/outreach/adhoc-draft — drafts a cold email for the
// free-form-email path on /outreach/sent?tab=create. Folder is required
// (the FE only enables the Generate button once a Service is picked).
export type OutreachAdhocDraftRequest = {
  folder_id: number;
  // Optional: the draft never reads the address (folder + name only), so
  // the firm-detail People section can draft for contacts with no email
  // yet. The send path still requires a real address.
  recipient_email?: string | null;
  recipient_name?: string | null;
};

// GET /api/v1/outreach/linked-providers — used by the Outreach modal to
// decide whether to render a provider picker (2+ linked), a "Connect"
// CTA (0 linked), or just use the only linked provider implicitly
// (1 linked).
export type LinkedProviderItem = {
  // PK of the ``account`` row -- one entry per linked account, not
  // per provider type (a user can link multiple Google accounts).
  // FE passes this back as ``sender_account_id`` on the send call.
  account_id: string;
  // OAuth provider's external user id. Used by Better Auth's
  // /unlink-account endpoint to disambiguate which account to drop
  // when the user has multiple of the same provider linked.
  provider_account_id: string;
  provider: EmailProviderId;
  // The mailbox the OAuth token is bound to (e.g. "alice@firm.com").
  // Null for legacy accounts linked before the post-link hook --
  // the picker labels those by provider name in that case.
  email_address: string | null;
  scope: string | null;
  has_send_scope: boolean;
  linked_at: string;
};

export type LinkedProvidersResponse = {
  items: LinkedProviderItem[];
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

// ── Outreach drafts (saved-but-unsent composer drafts) ───────────────
// The "Drafts" tab. Named with a `Saved` prefix to distinguish from
// `OutreachDraft` above, which is the AI-generate {subject, body} result.
// Recipients mirror the compose-send shape so a draft loads straight back
// into the To/Cc/Bcc composer. Maps to backend OutreachDraft* schemas.

// Body for POST /outreach/drafts and PUT /outreach/drafts/{id}. Every field
// is optional so a blank / partially-filled draft saves.
export type SavedOutreachDraftSaveRequest = {
  subject?: string;
  body?: string;
  to?: OutreachComposeRecipient[];
  cc?: string[];
  bcc?: string[];
  sender_account_id?: string | null;
  folder_id?: number | null;
  source?: "manual" | "doxie";
};

// One row in the Drafts list. Body is omitted (fetch the detail on open).
export type SavedOutreachDraft = {
  id: number;
  subject: string;
  to: OutreachComposeRecipient[];
  cc: string[];
  bcc: string[];
  folder_id: number | null;
  folder_name: string | null;
  sender_account_id: string | null;
  source: string;
  created_at: string;
  updated_at: string;
};

export type SavedOutreachDraftDetail = SavedOutreachDraft & {
  body: string;
};

export type SavedOutreachDraftsListResponse = {
  items: SavedOutreachDraft[];
  total: number;
  limit: number;
  offset: number;
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

// Unified per-user activity feed used by /settings/users/{id}/activities.
// `event_type` discriminates the row glyph + tooltip. `target_*` is set
// when the activity is bound to a firm (view, save, outreach); login /
// logout rows leave it null. `details` carries event-specific extras
// (login → ip + user_agent; save → list_name; view → visit_count;
// outreach → status + error + subject).
export type AdminUserActivityEventType =
  | "login"
  | "logout"
  | "view"
  | "save"
  | "outreach"
  | "nav_view"
  | "nav_click"
  | "link_open"
  | "search_query"
  | "input_used"
  | "doxie";

// Query-string ``?type=`` value when calling
// /api/v1/users/{id}/activities. Granular event_type rows are
// collapsed into family chips on the FE — login+logout → "login",
// nav_*+link_open → "nav", search_query → "search", input_used →
// "input". Doxie chat rows map 1:1 to the "doxie" chip.
export type AdminUserActivityFilter =
  | "login"
  | "view"
  | "save"
  | "outreach"
  | "nav"
  | "search"
  | "input"
  | "doxie";

export type AdminUserActivityTargetType =
  | "broker_dealer"
  | "advisor"
  | "institutional_investor";

export type AdminUserActivityRow = {
  event_type: AdminUserActivityEventType;
  timestamp: string;
  target_type: AdminUserActivityTargetType | null;
  target_id: number | null;
  target_name: string | null;
  details: Record<string, unknown> | null;
};

export type AdminUserActivitiesResponse = {
  items: AdminUserActivityRow[];
  total: number;
  limit: number;
  offset: number;
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
  // 'iapd' | 'apollo' | 'serpapi' | null
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
  // Clearing-agency / SRO membership labels (same shape as BD). Mostly empty
  // for IAs — only dually-registered BD/IA firms match.
  member_agencies: string[];
  clearing_membership_checked_at: string | null;
};

export type InvestmentAdvisorDetail = InvestmentAdvisorListItem & {
  // Form ADV Schedule D §1.B "Other Business Names" (IAPD
  // basicInformation.otherNames), filtered to drop the firm's own
  // primary/legal name. Detail-only — not on the list item. Rendered as
  // "Alternative Names" on the advisor detail page.
  other_business_names: string[] | null;
};

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
  emails: EmailHit[];
  phones: PhoneHit[];
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
  // Full clearing-agency / SRO membership rows with provenance (active +
  // needs_review). Mostly empty for IAs.
  clearing_memberships: ClearingMembershipItem[];
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
  emails: EmailHit[];
  phones: PhoneHit[];
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
  provider?: EmailProviderId;
  sender_account_id?: string | null;
};

export type OutreachInvestorDraftRequest = {
  institutional_investor_id: number;
  investor_contact_id: number;
  folder_id: number;
};

export type OutreachInvestorSendRequest = OutreachInvestorDraftRequest & {
  subject: string;
  body: string;
  provider?: EmailProviderId;
  sender_account_id?: string | null;
};
