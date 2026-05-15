import { apiRequest, buildApiPath } from "@/lib/api";

// Mirror of backend/app/schemas/email_extractor.py — shared by the
// /email-extractor standalone page and the inline "Discovered emails"
// section on /master-list/{id}.

export type RunStatus = "queued" | "running" | "completed" | "failed";
export type EnrichmentStatus =
  | "not_enriched"
  | "enriched"
  | "no_match"
  | "error";

export interface EmailVerificationResponse {
  id: number;
  syntax_valid: boolean | null;
  mx_record_present: boolean | null;
  smtp_status: string;
  smtp_message: string | null;
  checked_at: string;
}

export interface DiscoveredEmailResponse {
  id: number;
  email: string;
  domain: string;
  source: string;
  confidence: number | null;
  attribution: string | null;
  bd_id: number | null;
  enriched_name: string | null;
  enriched_title: string | null;
  enriched_linkedin_url: string | null;
  enriched_company: string | null;
  enriched_phone: string | null;
  enriched_at: string | null;
  enrichment_status: EnrichmentStatus;
  created_at: string;
  verifications: EmailVerificationResponse[];
}

export interface ScanResponse {
  id: number;
  pipeline_name: string;
  domain: string;
  person_name: string | null;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  discovered_emails: DiscoveredEmailResponse[];
}

export interface ScanListItem {
  id: number;
  domain: string;
  person_name: string | null;
  bd_id: number | null;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface VerifyResultItem {
  email_id: number;
  email: string | null;
  smtp_status: string;
  smtp_message: string | null;
  checked_at: string;
}

export interface VerificationRunCreateResponse {
  verify_run_id: number;
  status: RunStatus;
}

export interface VerificationRunResponse {
  id: number;
  status: RunStatus;
  total_items: number;
  processed_items: number;
  success_count: number;
  failure_count: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  results: VerifyResultItem[];
}

export interface EnrichAllResponse {
  scan_id: number;
  candidates_total: number;
  candidates_skipped_already_enriched: number;
  candidates_queued: number;
  status: "queued";
}

// Polling cadence + terminal-state helpers shared by the scan-detail view
// (1.5s scan poll, 1.5s verify poll, 3-minute hard cap).
export const POLL_INTERVAL_MS = 1500;
export const POLL_TIMEOUT_MS = 180_000;
export const TERMINAL_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "completed",
  "failed",
]);

// ── API wrappers ──────────────────────────────────────────────────────────

export async function getScan(scanId: number): Promise<ScanResponse> {
  return apiRequest<ScanResponse>(`/api/v1/email-extractor/scans/${scanId}`);
}

export async function listScansForBrokerDealer(
  bdId: number,
  limit = 1
): Promise<ScanListItem[]> {
  return apiRequest<ScanListItem[]>(
    buildApiPath("/api/v1/email-extractor/scans", { bd_id: bdId, limit })
  );
}

export async function enrichEmail(
  emailId: number
): Promise<DiscoveredEmailResponse> {
  return apiRequest<DiscoveredEmailResponse>(
    `/api/v1/email-extractor/discovered-emails/${emailId}/enrich`,
    { method: "POST" }
  );
}

export async function enrichAll(scanId: number): Promise<EnrichAllResponse> {
  return apiRequest<EnrichAllResponse>(
    `/api/v1/email-extractor/scans/${scanId}/enrich-all`,
    { method: "POST" }
  );
}

export async function verifyEmail(
  emailId: number
): Promise<VerificationRunCreateResponse> {
  return apiRequest<VerificationRunCreateResponse>(
    "/api/v1/email-extractor/verify",
    {
      method: "POST",
      body: JSON.stringify({ email_ids: [emailId] }),
    }
  );
}

export async function getVerifyRun(
  verifyRunId: number
): Promise<VerificationRunResponse> {
  return apiRequest<VerificationRunResponse>(
    `/api/v1/email-extractor/verify-runs/${verifyRunId}`
  );
}
