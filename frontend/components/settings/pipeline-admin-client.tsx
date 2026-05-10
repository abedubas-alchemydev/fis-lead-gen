"use client";

import { useEffect, useState, useTransition } from "react";

import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Inbox,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { apiRequest, buildApiPath } from "@/lib/api";
import type {
  ClearingArrangementItem,
  CompetitorProviderCreate,
  CompetitorProvidersResponse,
  DataRefreshResponse,
  PipelineStatusResponse,
  PipelineTriggerResponse,
  ScoringSettingsItem
} from "@/lib/types";

// ── Design tokens — match /dashboard + /master-list + /alerts. ──────────────
// Soft-card surface uses the same CSS-var palette as kpi-card / alert-feed-card,
// so light + dark themes both read clean. Fallbacks keep components viewable
// in design previews where the var ladder isn't loaded.
const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const INPUT_CLASS =
  "w-full rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] transition focus:border-[var(--accent,#6366f1)] focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30";

// Status pill catalog — mirrors the priority palette used by /alerts so
// the "Running" / "Completed" / "Failed" / "Idle" tags read consistently
// across the app.
type RunStatus = "running" | "completed" | "failed" | "idle";
const STATUS_STYLE: Record<
  RunStatus,
  { label: string; pill: string; dot: string; pulse: boolean }
> = {
  running: {
    label: "Running",
    pill: "bg-blue-500/12 text-[var(--pill-blue-text,#1d4ed8)] border-blue-500/25",
    dot: "bg-blue-500",
    pulse: true
  },
  completed: {
    label: "Completed",
    pill: "bg-emerald-500/12 text-[var(--pill-green-text,#047857)] border-emerald-500/25",
    dot: "bg-emerald-500",
    pulse: false
  },
  failed: {
    label: "Failed",
    pill: "bg-red-500/12 text-[var(--pill-red-text,#b91c1c)] border-red-500/25",
    dot: "bg-red-500",
    pulse: false
  },
  idle: {
    label: "Idle",
    pill: "bg-slate-100 text-slate-600 border-slate-200",
    dot: "bg-slate-400",
    pulse: false
  }
};

function resolveStatus(raw: string | undefined | null): RunStatus {
  if (raw === "running" || raw === "completed" || raw === "failed" || raw === "idle") {
    return raw;
  }
  return "idle";
}

const SCORING_FIELDS: ReadonlyArray<readonly [string, keyof ScoringSettingsItem]> = [
  ["Net Capital Growth", "net_capital_growth_weight"],
  ["Clearing Arrangement", "clearing_arrangement_weight"],
  ["Financial Health", "financial_health_weight"],
  ["Registration Recency", "registration_recency_weight"]
] as const;

export function PipelineAdminClient() {
  const [status, setStatus] = useState<PipelineStatusResponse | null>(null);
  const [competitors, setCompetitors] = useState<CompetitorProvidersResponse["items"]>([]);
  const [scoring, setScoring] = useState<ScoringSettingsItem | null>(null);
  const [newCompetitorName, setNewCompetitorName] = useState("");
  const [newCompetitorAliases, setNewCompetitorAliases] = useState("");
  const [newCompetitorPriority, setNewCompetitorPriority] = useState(90);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  async function loadData() {
    try {
      const [pipelineStatus, competitorResponse, scoringResponse] = await Promise.all([
        apiRequest<PipelineStatusResponse>("/api/v1/pipeline/clearing"),
        apiRequest<CompetitorProvidersResponse>("/api/v1/settings/competitors"),
        apiRequest<ScoringSettingsItem>("/api/v1/settings/scoring")
      ]);
      setStatus(pipelineStatus);
      setCompetitors(competitorResponse.items);
      setScoring(scoringResponse);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load pipeline status.");
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  function runAction(path: string) {
    startTransition(async () => {
      try {
        await apiRequest<PipelineTriggerResponse>(path, { method: "POST" });
        await loadData();
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Unable to trigger pipeline.");
        }
      });
  }

  function updateScoringField<K extends keyof ScoringSettingsItem>(key: K, value: ScoringSettingsItem[K]) {
    setScoring((current) => (current ? { ...current, [key]: value } : current));
  }

  function saveScoring() {
    if (!scoring) {
      return;
    }
    startTransition(async () => {
      try {
        await apiRequest<ScoringSettingsItem>("/api/v1/settings/scoring", {
          method: "PUT",
          body: JSON.stringify({
            net_capital_growth_weight: scoring.net_capital_growth_weight,
            clearing_arrangement_weight: scoring.clearing_arrangement_weight,
            financial_health_weight: scoring.financial_health_weight,
            registration_recency_weight: scoring.registration_recency_weight
          })
        });
        await loadData();
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Unable to save scoring settings.");
      }
    });
  }

  function createCompetitor() {
    startTransition(async () => {
      try {
        await apiRequest<CompetitorProviderCreate>("/api/v1/settings/competitors", {
          method: "POST",
          body: JSON.stringify({
            name: newCompetitorName,
            aliases: newCompetitorAliases
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
            priority: newCompetitorPriority
          })
        });
        setNewCompetitorName("");
        setNewCompetitorAliases("");
        setNewCompetitorPriority(90);
        await loadData();
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Unable to create competitor.");
      }
    });
  }

  function saveCompetitor(id: number, aliases: string[], priority: number, isActive: boolean) {
    startTransition(async () => {
      try {
        await apiRequest(`/api/v1/settings/competitors/${id}`, {
          method: "PUT",
          body: JSON.stringify({
            aliases,
            priority,
            is_active: isActive
          })
        });
        await loadData();
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Unable to update competitor.");
      }
    });
  }

  function refreshData() {
    startTransition(async () => {
      try {
        await apiRequest<DataRefreshResponse>("/api/v1/settings/refresh-data", { method: "POST" });
        await loadData();
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Unable to refresh data.");
      }
    });
  }

  const totalScoring = scoring
    ? scoring.net_capital_growth_weight +
      scoring.clearing_arrangement_weight +
      scoring.financial_health_weight +
      scoring.registration_recency_weight
    : 0;

  const statusKey = resolveStatus(status?.latest_run?.status);
  const statusStyle = STATUS_STYLE[statusKey];

  return (
    <section className="space-y-6">
      {/* Page header — mirrors /dashboard topbar typography. */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span> Settings
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Settings and controlled refresh
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Adjust the weighted lead-scoring model, maintain the competitor provider list, and trigger a controlled refresh of the alert and clearing pipelines.
          </p>
        </div>
      </div>

      {/* Admin Controls — soft-card with primary / secondary / tertiary buttons. */}
      <div className={CARD}>
        <p className={EYEBROW}>Admin Controls</p>
        <h2 className={CARD_TITLE}>Pipeline actions</h2>
        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            disabled={isPending}
            onClick={refreshData}
            className="inline-flex items-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none"
          >
            {isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden />
            )}
            Refresh data
          </button>
          <button
            type="button"
            disabled={isPending}
            onClick={() => runAction("/api/v1/settings/refresh-finra-details")}
            className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-4 py-2.5 text-sm font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            Refresh FINRA details
          </button>
          <button
            type="button"
            disabled={isPending}
            onClick={() => runAction("/api/v1/pipeline/clearing/retry-failed")}
            className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            Retry failed
          </button>
        </div>
        <p className="mt-3 text-xs text-[var(--text-muted,#94a3b8)]">
          &ldquo;Refresh FINRA details&rdquo; re-scans all firms for updated owners, officers, and business types (bi-monthly recommended).
        </p>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {/* Latest Run + Scoring Weights — 2-col at md+, stack below. */}
      <div className="grid gap-6 md:grid-cols-2">
        <div className={CARD}>
          <div className="flex items-center justify-between gap-3">
            <p className={EYEBROW}>Latest Run</p>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] ${statusStyle.pill}`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${statusStyle.dot} ${statusStyle.pulse ? "animate-pulse" : ""}`}
                aria-hidden
              />
              {statusStyle.label}
            </span>
          </div>
          {!status?.latest_run ? (
            <EmptyState
              icon={<Inbox className="h-6 w-6" strokeWidth={1.75} aria-hidden />}
              title="No pipeline run yet"
              body="Trigger a refresh from the actions above to see run results here."
            />
          ) : (
            <div className="mt-4 space-y-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Tile label="Status" value={status.latest_run.status} valueClass="capitalize" />
                <Tile
                  label="Processed"
                  value={`${status.latest_run.processed_items}/${status.latest_run.total_items}`}
                />
                <Tile
                  label="Successes"
                  value={String(status.latest_run.success_count)}
                  valueClass="text-emerald-600"
                />
                <Tile
                  label="Flagged"
                  value={String(status.latest_run.failure_count)}
                  valueClass="text-red-600"
                />
              </div>

              <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                <p>
                  <span className="font-semibold text-[var(--text,#0f172a)]">Triggered by:</span>{" "}
                  {status.latest_run.trigger_source}
                </p>
                <p className="mt-1.5">
                  <span className="font-semibold text-[var(--text,#0f172a)]">Started:</span>{" "}
                  {new Date(status.latest_run.started_at).toLocaleString()}
                </p>
                {status.latest_run.completed_at ? (
                  <p className="mt-1.5">
                    <span className="font-semibold text-[var(--text,#0f172a)]">Completed:</span>{" "}
                    {new Date(status.latest_run.completed_at).toLocaleString()}
                  </p>
                ) : null}
                {status.latest_run.notes ? <p className="mt-1.5">{status.latest_run.notes}</p> : null}
              </div>

              <div>
                <p className={EYEBROW}>Recent failures</p>
                <div className="mt-2 space-y-2">
                  {status.recent_failures.length === 0 ? (
                    <EmptyState
                      compact
                      icon={<CheckCircle2 className="h-5 w-5 text-emerald-600" strokeWidth={2} aria-hidden />}
                      title="No failures in the latest run"
                      body="All extractions passed integrity checks."
                    />
                  ) : (
                    status.recent_failures.map((item) => <FailureCard key={item.id} item={item} />)
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className={CARD}>
          <p className={EYEBROW}>Scoring Weights</p>
          <h2 className={CARD_TITLE}>Lead-priority weighting</h2>
          {scoring ? (
            <div className="mt-4 space-y-4">
              {SCORING_FIELDS.map(([label, key]) => {
                const value = scoring[key] as number;
                return (
                  <div key={String(key)}>
                    <div className="flex items-center justify-between text-sm">
                      <label
                        htmlFor={`scoring-${String(key)}`}
                        className="font-medium text-[var(--text,#0f172a)]"
                      >
                        {label}
                      </label>
                      <span className="font-mono text-xs text-[var(--text-dim,#475569)]">{value}%</span>
                    </div>
                    <input
                      id={`scoring-${String(key)}`}
                      type="range"
                      min={0}
                      max={100}
                      value={value}
                      onChange={(event) => updateScoringField(key, Number(event.target.value))}
                      className="mt-2 w-full cursor-pointer accent-[var(--accent,#6366f1)]"
                    />
                  </div>
                );
              })}

              <div className="flex items-center justify-between rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3">
                <span className="text-sm font-medium text-[var(--text,#0f172a)]">Total</span>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                    totalScoring === 100
                      ? "bg-emerald-500/12 text-[var(--pill-green-text,#047857)] border-emerald-500/25"
                      : "bg-red-500/12 text-[var(--pill-red-text,#b91c1c)] border-red-500/25"
                  }`}
                >
                  {totalScoring === 100 ? (
                    <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
                  ) : (
                    <AlertCircle className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
                  )}
                  {totalScoring}%
                </span>
              </div>

              <button
                type="button"
                disabled={isPending || totalScoring !== 100}
                onClick={saveScoring}
                className="inline-flex items-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-4 py-2 text-sm font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none"
              >
                {isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                Save scoring
              </button>
              {totalScoring !== 100 ? (
                <p className="text-xs text-[var(--pill-red-text,#b91c1c)]">
                  Weights must sum to exactly 100% before saving.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="mt-4 text-sm text-[var(--text-muted,#94a3b8)]">Loading scoring settings…</p>
          )}
        </div>
      </div>

      {/* Competitors + Add Competitor — same 2-col grid. */}
      <div className="grid gap-6 md:grid-cols-[1.1fr_0.9fr]">
        <div className={CARD}>
          <p className={EYEBROW}>Competitors</p>
          <h2 className={CARD_TITLE}>Provider catalog</h2>
          <div className="mt-4 space-y-3">
            {competitors.length === 0 ? (
              <EmptyState
                compact
                icon={<Inbox className="h-5 w-5" strokeWidth={1.75} aria-hidden />}
                title="No providers configured"
                body="Add your first competitor on the right."
              />
            ) : (
              competitors.map((item) => <CompetitorEditor key={item.id} item={item} onSave={saveCompetitor} />)
            )}
          </div>
        </div>

        <div className={CARD}>
          <p className={EYEBROW}>Add Competitor</p>
          <h2 className={CARD_TITLE}>New provider</h2>
          <div className="mt-4 space-y-3">
            <FieldLabel label="Provider name">
              <input
                value={newCompetitorName}
                onChange={(event) => setNewCompetitorName(event.target.value)}
                className={INPUT_CLASS}
              />
            </FieldLabel>
            <FieldLabel label="Aliases">
              <input
                value={newCompetitorAliases}
                onChange={(event) => setNewCompetitorAliases(event.target.value)}
                placeholder="Comma separated aliases"
                className={INPUT_CLASS}
              />
            </FieldLabel>
            <FieldLabel label="Priority">
              <input
                type="number"
                value={newCompetitorPriority}
                onChange={(event) => setNewCompetitorPriority(Number(event.target.value))}
                className={INPUT_CLASS}
              />
            </FieldLabel>
            <button
              type="button"
              disabled={isPending || !newCompetitorName.trim()}
              onClick={createCompetitor}
              className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-4 py-2 text-sm font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              Add competitor
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function Tile({
  label,
  value,
  valueClass = ""
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p className={`mt-1.5 text-base font-semibold text-[var(--text,#0f172a)] ${valueClass}`}>
        {value}
      </p>
    </div>
  );
}

function EmptyState({
  icon,
  title,
  body,
  compact = false
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] text-center ${
        compact ? "px-4 py-5" : "px-4 py-8"
      }`}
    >
      <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
        {icon}
      </div>
      <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">{title}</p>
      <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">{body}</p>
    </div>
  );
}

function FailureCard({ item }: { item: ClearingArrangementItem }) {
  const [expanded, setExpanded] = useState(false);
  const note = item.extraction_notes ?? item.extraction_status;
  const isLong = note.length > 160;

  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] border-l-4 border-l-red-500/40 bg-[var(--surface,#ffffff)] px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
          {item.clearing_partner ?? "Unknown partner"}
        </p>
        <span className="rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-[var(--text-dim,#475569)]">
          {item.clearing_type ?? "unknown"}
        </span>
        <span className="rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted,#94a3b8)]">
          BD #{item.bd_id}
        </span>
      </div>
      <p
        className={`mt-1.5 text-[13px] leading-5 text-[var(--text-dim,#475569)] ${
          isLong && !expanded ? "line-clamp-3" : ""
        }`}
      >
        {note}
      </p>
      {isLong ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1.5 inline-flex items-center gap-1 text-xs font-semibold text-[var(--accent,#6366f1)] transition hover:brightness-110"
        >
          {expanded ? (
            <>
              Show less
              <ChevronUp className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </>
          ) : (
            <>
              Show more
              <ChevronDown className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </>
          )}
        </button>
      ) : null}
    </div>
  );
}

function FieldLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function CompetitorEditor({
  item,
  onSave
}: {
  item: CompetitorProvidersResponse["items"][number];
  onSave: (id: number, aliases: string[], priority: number, isActive: boolean) => void;
}) {
  const [aliases, setAliases] = useState(item.aliases.join(", "));
  const [priority, setPriority] = useState(item.priority);
  const [isActive, setIsActive] = useState(item.is_active);

  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold text-[var(--text,#0f172a)]">{item.name}</p>
        <label className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Active
          <input
            type="checkbox"
            checked={isActive}
            onChange={(event) => setIsActive(event.target.checked)}
            className="h-4 w-4 cursor-pointer accent-[var(--accent,#6366f1)]"
          />
        </label>
      </div>
      <div className="mt-3 grid gap-2.5">
        <input
          value={aliases}
          onChange={(event) => setAliases(event.target.value)}
          placeholder="Aliases (comma separated)"
          className={INPUT_CLASS}
        />
        <input
          type="number"
          value={priority}
          onChange={(event) => setPriority(Number(event.target.value))}
          className={INPUT_CLASS}
        />
        <button
          type="button"
          onClick={() =>
            onSave(
              item.id,
              aliases
                .split(",")
                .map((part) => part.trim())
                .filter(Boolean),
              priority,
              isActive
            )
          }
          className="inline-flex w-fit items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-3 py-1.5 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          Save provider
        </button>
      </div>
    </div>
  );
}
