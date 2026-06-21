// API client for the Outreach Contacts page (/outreach/contacts).
// Mirrors backend/app/schemas/outreach_contacts.py.

import { apiRequest } from "@/lib/api";

export type OutreachEntityKind =
  | "broker_dealer"
  | "advisor"
  | "institutional_investor";

export interface OutreachContactsFirmRow {
  entity_kind: OutreachEntityKind;
  entity_id: number;
  name: string;
  contact_count: number;
  with_email_count: number;
  with_phone_count: number;
  // Count of Email-Extractor discovered_email rows linked to this firm. A
  // parallel source to the typed contact triad above (kept separate, never
  // folded in) -- powers the "N extracted" summary pill and the lazy-loaded
  // "Extracted emails" sub-section on expand. Firms now appear in the list
  // when contact_count > 0 OR discovered_email_count > 0.
  discovered_email_count: number;
  last_enriched_at: string | null;
  last_gap_fill_attempt_at: string | null;
  gap_fill_in_progress: boolean;
}

export interface OutreachContactsFirmsResponse {
  items: OutreachContactsFirmRow[];
  total: number;
}

export interface OutreachContactPerson {
  contact_id: number;
  name: string;
  title: string | null;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  enriched_at: string | null;
}

export interface OutreachContactsFirmDetailResponse {
  entity_kind: OutreachEntityKind;
  entity_id: number;
  entity_name: string;
  items: OutreachContactPerson[];
}

// An Email-Extractor discovered_email row scoped to a firm. Surfaced beneath
// the typed contacts in a separate "Extracted emails" sub-section. The
// /discovered-emails endpoint returns a bare array (no envelope), unlike the
// /persons response above.
export interface DiscoveredContactRow {
  id: number;
  email: string;
  enriched_name: string | null;
  enriched_title: string | null;
  enriched_phone: string | null;
  enriched_linkedin_url: string | null;
  enrichment_status: string;
  source: string | null;
  confidence: number | null;
  created_at: string;
}

export interface GapFillFirmResponse {
  run_id: number | null;
  status: string;
  entity_kind: OutreachEntityKind;
  entity_id: number;
  reason?: string | null;
}

export interface ListOutreachContactsFirmsParams {
  entity_kind?: OutreachEntityKind;
  q?: string;
  page?: number;
  limit?: number;
}

export async function listOutreachContactsFirms(
  params: ListOutreachContactsFirmsParams = {},
): Promise<OutreachContactsFirmsResponse> {
  const search = new URLSearchParams();
  if (params.entity_kind) search.set("entity_kind", params.entity_kind);
  if (params.q) search.set("q", params.q);
  if (params.page) search.set("page", String(params.page));
  if (params.limit) search.set("limit", String(params.limit));
  const qs = search.toString();
  const url = qs
    ? `/api/v1/outreach/contacts/firms?${qs}`
    : "/api/v1/outreach/contacts/firms";
  return apiRequest<OutreachContactsFirmsResponse>(url);
}

export async function listOutreachContactsFirmPersons(
  kind: OutreachEntityKind,
  id: number,
): Promise<OutreachContactsFirmDetailResponse> {
  return apiRequest<OutreachContactsFirmDetailResponse>(
    `/api/v1/outreach/contacts/firms/${kind}/${id}/persons`,
  );
}

// Pass the SAME `kind` the firm row carries (the persons wrapper above uses
// it too). Returns a bare array of discovered emails for the firm.
export async function listOutreachContactsFirmDiscoveredEmails(
  kind: OutreachEntityKind,
  id: number,
): Promise<DiscoveredContactRow[]> {
  return apiRequest<DiscoveredContactRow[]>(
    `/api/v1/outreach/contacts/firms/${kind}/${id}/discovered-emails`,
  );
}

export async function gapFillFirmContacts(
  kind: OutreachEntityKind,
  id: number,
): Promise<GapFillFirmResponse> {
  return apiRequest<GapFillFirmResponse>(
    `/api/v1/outreach/contacts/firms/${kind}/${id}/gap-fill`,
    { method: "POST" },
  );
}
