// API client for the Extraction Analytics page (/settings/extractions).
// Mirrors backend/app/schemas/extraction_analytics.py.

import { apiRequest, buildApiPath } from "@/lib/api";

export type FirmType = "broker_dealer" | "advisor" | "institutional_investor";
export type Surface = "contact" | "discovered_email" | "website";
export type ValueKind = "email" | "phone" | "linkedin" | "website";

export interface ProviderCount {
  provider: string;
  label: string;
  surface: Surface;
  firm_count: number;
  value_count: number;
}

export interface ProviderSummaryTotals {
  total_contacts_attributed: number;
  total_contacts_unattributed: number;
  total_discovered_emails: number;
  total_enriched_emails: number;
  total_firms_with_website: number;
}

export interface ProviderSummaryResponse {
  contacts: ProviderCount[];
  discovered_emails: ProviderCount[];
  websites: ProviderCount[];
  totals: ProviderSummaryTotals;
}

export interface FirmProviderCount {
  provider: string;
  label: string;
  count: number;
}

export interface FirmExtractionRow {
  firm_type: FirmType;
  firm_id: number;
  name: string;
  crd_number: string | null;
  website_source: string | null;
  contact_total: number;
  discovered_email_total: number;
  provider_counts: FirmProviderCount[];
  last_enriched_at: string | null;
}

export interface FirmExtractionListResponse {
  items: FirmExtractionRow[];
  total: number;
}

export interface ExtractedValue {
  kind: ValueKind;
  value: string;
  provider: string;
  label: string;
  contact_id: number | null;
  contact_name: string | null;
  contact_title: string | null;
  confidence: number | null;
  source_table: "contact" | "discovered_email" | "firm";
}

export interface FirmExtractionDetailResponse {
  firm_type: FirmType;
  firm_id: number;
  name: string;
  website: string | null;
  website_source: string | null;
  values: ExtractedValue[];
}

export async function getExtractionSummary(): Promise<ProviderSummaryResponse> {
  return apiRequest<ProviderSummaryResponse>(
    "/api/v1/extraction-analytics/summary",
  );
}

export interface ListExtractionFirmsParams {
  firm_type?: FirmType;
  provider?: string;
  q?: string;
  page?: number;
  limit?: number;
}

export async function listExtractionFirms(
  params: ListExtractionFirmsParams = {},
): Promise<FirmExtractionListResponse> {
  return apiRequest<FirmExtractionListResponse>(
    buildApiPath("/api/v1/extraction-analytics/firms", {
      firm_type: params.firm_type,
      provider: params.provider,
      q: params.q,
      page: params.page,
      limit: params.limit,
    }),
  );
}

export async function getFirmExtractionValues(
  firmType: FirmType,
  firmId: number,
): Promise<FirmExtractionDetailResponse> {
  return apiRequest<FirmExtractionDetailResponse>(
    `/api/v1/extraction-analytics/firms/${firmType}/${firmId}/values`,
  );
}
