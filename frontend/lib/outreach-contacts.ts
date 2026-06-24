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

// Discriminates the origin of an email-search row: a typed contact row
// (ExecutiveContact / AdvisorContact / InvestorContact) vs. an
// Email-Extractor discovered_email row. Mirrors the backend EmailSource.
export type OutreachEmailSource = "contact" | "extracted";

// One flat email row returned by the contacts page "email mode" search
// (GET /outreach/contacts/email-search). Mirrors
// backend/app/schemas/outreach_contacts.py::EmailSearchResult. Unlike the
// firm-card path, this is a per-email row attributed to its owning firm via
// entity_kind + entity_id + firm_name. `phone` is only set on a typed
// contact (source="contact"); `contact_id` is set on the same.
export interface EmailSearchResult {
  entity_kind: OutreachEntityKind;
  entity_id: number;
  firm_name: string;
  owner_name: string | null;
  title: string | null;
  email: string;
  phone: string | null;
  source: OutreachEmailSource;
  contact_id: number | null;
}

export interface EmailSearchResponse {
  results: EmailSearchResult[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export interface SearchOutreachContactsByEmailParams {
  q: string;
  page?: number;
  limit?: number;
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

// "Email mode" search for the contacts page: when the user's query is an
// email address, return the matching EMAIL rows directly instead of firm
// cards. Same fetch wrapper / credentials / error handling as the sibling
// fns above. A blank/whitespace q short-circuits to an empty page on the BE
// (no 422), but callers should only invoke this in email mode anyway.
export async function searchOutreachContactsByEmail(
  params: SearchOutreachContactsByEmailParams,
): Promise<EmailSearchResponse> {
  const search = new URLSearchParams();
  search.set("q", params.q);
  if (params.page) search.set("page", String(params.page));
  if (params.limit) search.set("limit", String(params.limit));
  return apiRequest<EmailSearchResponse>(
    `/api/v1/outreach/contacts/email-search?${search.toString()}`,
  );
}
