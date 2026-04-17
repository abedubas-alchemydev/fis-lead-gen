"use client";

import { useEffect, useState, useTransition } from "react";

import { apiRequest, buildApiPath } from "@/lib/api";
import type {
  CompetitorProviderCreate,
  CompetitorProvidersResponse,
  DataRefreshResponse,
  PipelineStatusResponse,
  PipelineTriggerResponse,
  ScoringSettingsItem
} from "@/lib/types";

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

  return (
    <section className="space-y-6">
      <div className="rounded-[30px] border border-white/80 bg-white/92 p-8 shadow-shell">
        <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">Admin Controls</p>
        <h1 className="mt-3 text-2xl font-semibold text-navy">Settings and controlled refresh</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
          Adjust the weighted lead scoring model, maintain the competitor provider list, and trigger a controlled refresh of the alert and clearing pipelines.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            disabled={isPending}
            onClick={refreshData}
            className="rounded-2xl bg-navy px-5 py-3 text-sm font-medium text-white disabled:opacity-60"
          >
            Refresh data
          </button>
          <button
            type="button"
            disabled={isPending}
            onClick={() => runAction("/api/v1/settings/refresh-finra-details")}
            className="rounded-2xl bg-blue px-5 py-3 text-sm font-medium text-white disabled:opacity-60"
          >
            Refresh FINRA details
          </button>
          <button
            type="button"
            disabled={isPending}
            onClick={() => runAction("/api/v1/pipeline/clearing/retry-failed")}
            className="rounded-2xl border border-slate-200 px-5 py-3 text-sm font-medium text-slate-700 disabled:opacity-60"
          >
            Retry failed
          </button>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          &ldquo;Refresh FINRA details&rdquo; re-scans all firms for updated owners, officers, and business types (bi-monthly recommended).
        </p>
      </div>

      {error ? <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">{error}</div> : null}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Latest Run</p>
          {!status?.latest_run ? (
            <p className="mt-4 text-sm text-slate-500">No pipeline runs recorded yet.</p>
          ) : (
            <div className="mt-4 space-y-4">
              <div className="grid gap-4 sm:grid-cols-4">
                <div className="rounded-2xl bg-slate-50 p-4">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Status</p>
                  <p className="mt-2 font-semibold capitalize text-navy">{status.latest_run.status}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 p-4">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Processed</p>
                  <p className="mt-2 font-semibold text-navy">
                    {status.latest_run.processed_items}/{status.latest_run.total_items}
                  </p>
                </div>
                <div className="rounded-2xl bg-slate-50 p-4">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Successes</p>
                  <p className="mt-2 font-semibold text-success">{status.latest_run.success_count}</p>
                </div>
                <div className="rounded-2xl bg-slate-50 p-4">
                  <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Flagged</p>
                  <p className="mt-2 font-semibold text-danger">{status.latest_run.failure_count}</p>
                </div>
              </div>

              <div className="rounded-2xl border border-slate-200 px-4 py-4 text-sm text-slate-600">
                <p><span className="font-medium text-navy">Triggered by:</span> {status.latest_run.trigger_source}</p>
                <p className="mt-2"><span className="font-medium text-navy">Started:</span> {new Date(status.latest_run.started_at).toLocaleString()}</p>
                {status.latest_run.completed_at ? (
                  <p className="mt-2"><span className="font-medium text-navy">Completed:</span> {new Date(status.latest_run.completed_at).toLocaleString()}</p>
                ) : null}
                {status.latest_run.notes ? <p className="mt-2">{status.latest_run.notes}</p> : null}
              </div>

              <div>
                <p className="text-sm font-medium text-navy">Recent failures</p>
                <div className="mt-3 space-y-2">
                  {status.recent_failures.length === 0 ? (
                    <p className="text-sm text-slate-500">No flagged extractions.</p>
                  ) : (
                    status.recent_failures.map((item) => (
                      <div key={item.id} className="rounded-2xl border border-slate-200 px-4 py-3 text-sm">
                        <p className="font-medium text-navy">
                          {item.clearing_partner ?? "Unknown partner"} • {item.clearing_type ?? "unknown"}
                        </p>
                        <p className="mt-1 text-slate-500">{item.extraction_notes ?? item.extraction_status}</p>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Scoring Weights</p>
          {scoring ? (
            <div className="mt-4 space-y-4">
              {[
                ["Net Capital Growth", "net_capital_growth_weight"],
                ["Clearing Arrangement", "clearing_arrangement_weight"],
                ["Financial Health", "financial_health_weight"],
                ["Registration Recency", "registration_recency_weight"]
              ].map(([label, key]) => (
                <label key={key} className="block text-sm font-medium text-slate-700">
                  {label}
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={scoring[key as keyof ScoringSettingsItem] as number}
                    onChange={(event) => updateScoringField(key as keyof ScoringSettingsItem, Number(event.target.value))}
                    className="mt-2 w-full"
                  />
                  <span className="text-xs text-slate-500">{scoring[key as keyof ScoringSettingsItem]}%</span>
                </label>
              ))}
              <div className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                Total:{" "}
                {scoring.net_capital_growth_weight +
                  scoring.clearing_arrangement_weight +
                  scoring.financial_health_weight +
                  scoring.registration_recency_weight}
                %
              </div>
              <button
                type="button"
                disabled={isPending}
                onClick={saveScoring}
                className="rounded-2xl bg-blue px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                Save scoring
              </button>
            </div>
          ) : (
            <p className="mt-4 text-sm text-slate-500">Loading scoring settings...</p>
          )}
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Competitors</p>
          <div className="mt-4 space-y-3">
            {competitors.map((item) => (
              <CompetitorEditor key={item.id} item={item} onSave={saveCompetitor} />
            ))}
          </div>
        </div>

        <div className="rounded-[30px] border border-white/80 bg-white/92 p-6 shadow-shell">
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-blue">Add Competitor</p>
          <div className="mt-4 space-y-3">
            <label className="block text-sm font-medium text-slate-700">
              Provider name
              <input
                value={newCompetitorName}
                onChange={(event) => setNewCompetitorName(event.target.value)}
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Aliases
              <input
                value={newCompetitorAliases}
                onChange={(event) => setNewCompetitorAliases(event.target.value)}
                placeholder="Comma separated aliases"
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Priority
              <input
                type="number"
                value={newCompetitorPriority}
                onChange={(event) => setNewCompetitorPriority(Number(event.target.value))}
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm"
              />
            </label>
            <button
              type="button"
              disabled={isPending || !newCompetitorName.trim()}
              onClick={createCompetitor}
              className="rounded-2xl bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              Add competitor
            </button>
          </div>
        </div>
      </div>
    </section>
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
    <div className="rounded-2xl border border-slate-200 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="font-medium text-navy">{item.name}</p>
        <label className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-slate-500">
          Active
          <input type="checkbox" checked={isActive} onChange={(event) => setIsActive(event.target.checked)} />
        </label>
      </div>
      <div className="mt-3 grid gap-3">
        <input
          value={aliases}
          onChange={(event) => setAliases(event.target.value)}
          className="rounded-2xl border border-slate-200 px-3 py-2 text-sm"
        />
        <input
          type="number"
          value={priority}
          onChange={(event) => setPriority(Number(event.target.value))}
          className="rounded-2xl border border-slate-200 px-3 py-2 text-sm"
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
          className="rounded-2xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700"
        >
          Save provider
        </button>
      </div>
    </div>
  );
}
