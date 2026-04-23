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
  health_status: string | null;
  is_deficient: boolean;
  latest_deficiency_filed_at: string | null;
  lead_score: number | null;
  lead_priority: string | null;
  current_clearing_partner: string | null;
  current_clearing_type: string | null;
  current_clearing_is_competitor: boolean;
  current_clearing_source_filing_url: string | null;
  current_clearing_extraction_confidence: number | null;
  last_audit_report_date: string | null;
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
  high_value_leads: number;
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

export type ExecutiveContactItem = {
  id: number;
  bd_id: number;
  name: string;
  title: string;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  source: string;
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

export type DataRefreshResponse = {
  filing_monitor_run_id: number;
  clearing_pipeline_run_id: number;
  refreshed_broker_dealers: number;
};

export type ExportPreviewResponse = {
  matching_records: number;
  export_limit: number;
  remaining_exports_today: number;
  requested_records: number;
};

export type ExportCsvResponse = {
  filename: string;
  content: string;
  exported_records: number;
  remaining_exports_today: number;
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
