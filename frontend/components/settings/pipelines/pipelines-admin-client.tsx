"use client";

import { useState } from "react";

import {
  runFilingMonitor,
  runInitialLoad,
  runPopulateAll,
} from "@/lib/api";

import { FreshRegenCard } from "./fresh-regen-card";
import { PipelineTriggerCard } from "./pipeline-trigger-card";
import { RecentRunsTable } from "./recent-runs-table";

// Page-level composition for /settings/pipelines. Owns a single refresh
// counter so each card can nudge the recent-runs table after a successful
// trigger. The server-component parent has already verified admin role,
// so this client tree is admin-gated by construction — no extra check
// needed here.

export function PipelinesAdminClient() {
  const [recentRunsKey, setRecentRunsKey] = useState(0);
  const bumpRecentRuns = () => setRecentRunsKey((current) => current + 1);

  return (
    <section className="space-y-6">
      {/* Page header — mirrors /settings + /dashboard typography. */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            Settings <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            Pipelines
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Pipeline triggers
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Trigger the three Tier 2 pipelines on demand instead of running
            the python scripts over SSH. Runs are async and admin-gated; the
            backend returns a run id immediately and continues processing in
            the background. Cloud Scheduler hits the same endpoints on a
            fixed cadence — these manual triggers are for ad-hoc refreshes.
          </p>
        </div>
      </div>

      <div className="grid gap-6">
        <PipelineTriggerCard
          pipelineName="Filing Monitor"
          cadence="Hourly Cloud Scheduler"
          eta="A few minutes"
          description="Picks up new SEC filings and routes priority items to /alerts. Run manually for an immediate refresh between scheduled runs."
          runAction={runFilingMonitor}
          onSuccess={bumpRecentRuns}
        />
        <PipelineTriggerCard
          pipelineName="Populate All Data"
          cadence="Weekly · Sunday 02:00 UTC"
          eta="30–90 minutes"
          description="Full enrichment refresh: financials, clearing arrangements, executives, and lead scoring. Heavy run — schedule deliberately."
          runAction={runPopulateAll}
          onSuccess={bumpRecentRuns}
        />
        <PipelineTriggerCard
          pipelineName="Initial Load"
          cadence="Weekly · Sunday 06:00 UTC"
          eta="15–30 minutes"
          description="Fetches newly-registered broker-dealers from FINRA so they show up in master list and the email-extractor flow."
          runAction={runInitialLoad}
          onSuccess={bumpRecentRuns}
        />
      </div>

      <div className="mt-4 flex items-center gap-4 pt-6" aria-hidden>
        <span className="h-px flex-1 bg-red-500/30" />
        <span className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--pill-red-text,#b91c1c)]">
          <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
          Destructive zone — manual only
          <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
        </span>
        <span className="h-px flex-1 bg-red-500/30" />
      </div>

      <FreshRegenCard onSuccess={bumpRecentRuns} />

      <RecentRunsTable refreshKey={recentRunsKey} />
    </section>
  );
}
