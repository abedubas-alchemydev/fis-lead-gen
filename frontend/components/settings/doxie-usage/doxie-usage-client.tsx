"use client";

import { AlertCircle, Coins, Gauge, MessageSquare, RefreshCw, Wrench } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { KpiCard, type KpiIconProps } from "@/components/dashboard/kpi-card";
import { SectionPanel } from "@/components/ui/section-panel";
import {
  getDoxieUsageSummary,
  listDoxieUsageUsers,
  type DoxieUsageSummary,
  type DoxieUserUsageRow,
} from "@/lib/doxie-usage";

const PAGE_SIZE = 50;

// Doxie costs are fractions of a cent per turn; the shared compact
// formatCurrency ($74.3M-style) collapses them to "$0". Show cents-grade:
// >=$1 to 2 dp, sub-$1 to 4 dp, both with thousands separators.
function formatUsd(value: number | null | undefined): string {
  const n = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.abs(n) < 1 ? 4 : 2,
  });
}

// KpiCard wants ComponentType<{className?; strokeWidth?: number}>. Lucide's
// own type widens strokeWidth to string|number, so wrap each icon to the
// exact shape rather than casting.
const MessageKpiIcon = (props: KpiIconProps) => <MessageSquare {...props} />;
const CoinsKpiIcon = (props: KpiIconProps) => <Coins {...props} />;
const WrenchKpiIcon = (props: KpiIconProps) => <Wrench {...props} />;
const GaugeKpiIcon = (props: KpiIconProps) => <Gauge {...props} />;

function formatLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

function displayName(row: DoxieUserUsageRow): string {
  return row.name?.trim() || row.email?.trim() || row.user_id;
}

export function DoxieUsageClient(): React.ReactElement {
  const [summary, setSummary] = useState<DoxieUsageSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [users, setUsers] = useState<DoxieUserUsageRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Summary loads once on mount — it's account-wide, not page-dependent.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await getDoxieUsageSummary();
        if (!cancelled) setSummary(response);
      } catch (err) {
        if (!cancelled) {
          setSummaryError(
            err instanceof Error ? err.message : "Could not load summary",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await listDoxieUsageUsers({ page, limit: PAGE_SIZE });
      setUsers(response.items);
      setTotal(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load users");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const costHelper = summary
    ? `$${summary.cost_input_per_1m_usd.toFixed(2)} in / $${summary.cost_output_per_1m_usd.toFixed(2)} out per 1M tokens`
    : "Loading…";

  const turnsHelper = summary
    ? `${summary.assistant_turns_with_tokens.toLocaleString()} with token data`
    : "Loading…";

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      {/* Topbar */}
      <div className="mb-7">
        <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
          Settings <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
          Doxie Usage
        </p>
        <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          Doxie token usage &amp; estimated cost
        </h1>
      </div>

      {summaryError ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>{summaryError}</span>
        </div>
      ) : null}

      {/* KPI strip */}
      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Total tokens"
          value={(summary?.total_tokens ?? 0).toLocaleString()}
          helper={
            summary
              ? `${summary.total_prompt_tokens.toLocaleString()} in · ${summary.total_completion_tokens.toLocaleString()} out`
              : "Loading…"
          }
          tone="blue"
          icon={MessageKpiIcon}
        />
        <KpiCard
          title="Estimated cost"
          value={formatUsd(summary?.estimated_cost_usd ?? 0)}
          helper={costHelper}
          tone="amber"
          icon={CoinsKpiIcon}
        />
        <KpiCard
          title="Tool calls"
          value={(summary?.total_tool_calls ?? 0).toLocaleString()}
          helper="Across all assistant turns"
          tone="purple"
          icon={WrenchKpiIcon}
        />
        <KpiCard
          title="Assistant turns"
          value={(summary?.total_assistant_turns ?? 0).toLocaleString()}
          helper={turnsHelper}
          tone="blue"
          icon={GaugeKpiIcon}
        />
      </div>

      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" strokeWidth={2} />
          <span>{error}</span>
        </div>
      ) : null}

      {/* Per-user table */}
      <SectionPanel
        eyebrow="Per user"
        title={`Usage by user${total ? ` (${total.toLocaleString()})` : ""}`}
        headerAction={
          <button
            type="button"
            onClick={() => void loadUsers()}
            disabled={loading}
            className={clsx(
              buttonBase,
              buttonSizes.sm,
              "border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]",
            )}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              strokeWidth={2}
            />
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      >
        <p className="mb-3 max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          Estimated cost is a flat-rate approximation from the configured
          per-token prices — not a billing figure. Turns where the model did
          not report token counts contribute 0 tokens but still count as a
          turn.
        </p>

        {loading && users.length === 0 ? (
          <div>
            {Array.from({ length: 6 }).map((_, index) => (
              <div
                key={`user-skeleton-${index}`}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <div className="h-3 w-40 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-4 w-64 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
            ))}
          </div>
        ) : users.length === 0 ? (
          <div className="py-8 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
            No Doxie usage recorded yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-[var(--border,rgba(30,64,175,0.1))] text-left text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  <th className="py-2 pr-3 font-semibold">User</th>
                  <th className="py-2 pr-3 text-right font-semibold">Turns</th>
                  <th className="py-2 pr-3 text-right font-semibold">Tokens (in / out)</th>
                  <th className="py-2 pr-3 text-right font-semibold">Total tokens</th>
                  <th className="py-2 pr-3 text-right font-semibold">Tools</th>
                  <th className="py-2 pr-3 text-right font-semibold">Est. cost</th>
                  <th className="py-2 pr-3 text-right font-semibold">Avg latency</th>
                  <th className="py-2 text-right font-semibold">p50 latency</th>
                </tr>
              </thead>
              <tbody>
                {users.map((row) => (
                  <tr
                    key={row.user_id}
                    className="border-b border-[var(--border,rgba(30,64,175,0.1))] last:border-b-0"
                  >
                    <td className="py-2.5 pr-3">
                      <div className="font-medium text-[var(--text,#0f172a)]">
                        {displayName(row)}
                      </div>
                      {row.email && row.email !== displayName(row) ? (
                        <div className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                          {row.email}
                        </div>
                      ) : null}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {row.assistant_turns.toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {row.total_prompt_tokens.toLocaleString()} /{" "}
                      {row.total_completion_tokens.toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums font-medium text-[var(--text,#0f172a)]">
                      {row.total_tokens.toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {row.total_tool_calls.toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {formatUsd(row.estimated_cost_usd)}
                    </td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {formatLatency(row.avg_latency_ms)}
                    </td>
                    <td className="py-2.5 text-right tabular-nums text-[var(--text-dim,#475569)]">
                      {formatLatency(row.p50_latency_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 ? (
          <div className="mt-4 flex items-center justify-between border-t border-[var(--border,rgba(30,64,175,0.1))] pt-4 text-[12px]">
            <span className="text-[var(--text-muted,#94a3b8)]">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
                className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
                className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </SectionPanel>
    </div>
  );
}
