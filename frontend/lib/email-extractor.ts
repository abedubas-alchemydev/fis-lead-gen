import { apiRequest } from "@/lib/api";

export interface EnrichAllResponse {
  scan_id: number;
  candidates_total: number;
  candidates_skipped_already_enriched: number;
  candidates_queued: number;
  status: "queued";
}

export async function enrichAll(scanId: number): Promise<EnrichAllResponse> {
  return apiRequest<EnrichAllResponse>(
    `/api/v1/email-extractor/scans/${scanId}/enrich-all`,
    { method: "POST" },
  );
}
