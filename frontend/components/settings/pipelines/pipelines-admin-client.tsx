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
      <header className="rounded-[30px] border border-white/80 bg-white/92 p-8 shadow-shell">
        <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">
          Admin Controls
        </p>
        <h1 className="mt-3 text-2xl font-semibold text-navy">Pipelines</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
          Trigger the three Tier 2 pipelines on demand instead of running
          the python scripts over SSH. Runs are async and admin-gated; the
          BE returns a run id immediately and continues processing in the
          background. Cloud Scheduler hits the same endpoints on a fixed
          cadence — these manual triggers are for ad-hoc refreshes.
        </p>
      </header>

      <div className="grid gap-5">
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

      <div className="mt-8 flex items-center gap-3" aria-hidden>
        <span className="h-px flex-1 bg-red-200" />
        <span className="text-[11px] font-medium uppercase tracking-[0.28em] text-danger">
          Destructive zone
        </span>
        <span className="h-px flex-1 bg-red-200" />
      </div>

      <FreshRegenCard onSuccess={bumpRecentRuns} />

      <RecentRunsTable refreshKey={recentRunsKey} />
    </section>
  );
}
