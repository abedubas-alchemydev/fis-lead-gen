// API client for the Doxie Usage / cost dashboard (/settings/doxie-usage).
// Mirrors backend/app/schemas/doxie_usage.py.

import { apiRequest, buildApiPath } from "@/lib/api";

export interface DoxieUsageSummary {
  total_assistant_turns: number;
  assistant_turns_with_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_tool_calls: number;
  estimated_cost_usd: number;
  cost_input_per_1m_usd: number;
  cost_output_per_1m_usd: number;
}

export interface DoxieUserUsageRow {
  user_id: string;
  name: string | null;
  email: string | null;
  assistant_turns: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_tool_calls: number;
  estimated_cost_usd: number;
  avg_latency_ms: number | null;
  p50_latency_ms: number | null;
}

export interface DoxieUserUsageListResponse {
  items: DoxieUserUsageRow[];
  total: number;
}

export async function getDoxieUsageSummary(): Promise<DoxieUsageSummary> {
  return apiRequest<DoxieUsageSummary>("/api/v1/doxie-usage/summary");
}

export interface ListDoxieUsageUsersParams {
  page?: number;
  limit?: number;
}

export async function listDoxieUsageUsers(
  params: ListDoxieUsageUsersParams = {},
): Promise<DoxieUserUsageListResponse> {
  return apiRequest<DoxieUserUsageListResponse>(
    buildApiPath("/api/v1/doxie-usage/users", {
      page: params.page,
      limit: params.limit,
    }),
  );
}
