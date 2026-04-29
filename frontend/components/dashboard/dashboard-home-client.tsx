"use client";

import { useCallback, useEffect, useState } from "react";

import { AlertFeedCard } from "@/components/alerts/alert-feed-card";
import { ClearingDistributionChart } from "@/components/dashboard/clearing-distribution-chart";
import { DashboardErrorCard } from "@/components/dashboard/dashboard-error-card";
import { KpiCard } from "@/components/dashboard/kpi-card";
import type { KpiIconProps } from "@/components/dashboard/kpi-card";
import { KpiCardSkeleton } from "@/components/dashboard/kpi-card-skeleton";
import { LeadVolumeTrendCard } from "@/components/dashboard/lead-volume-trend-card";
import { TopLeadsCard } from "@/components/dashboard/top-leads-card";
import { TopActions } from "@/components/layout/top-actions";
import { apiRequest } from "@/lib/api";
import type { AlertListItem, AlertListResponse, ClearingDistributionResponse, DashboardStats } from "@/lib/types";

// ─── KPI icons — verbatim SVG paths from dashboard-redesign.html ──────────

function KpiIconBuilding({ className, strokeWidth = 2 }: KpiIconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} className={className} aria-hidden>
      <path d="M3 21h18" />
      <path d="M5 21V7l7-4 7 4v14" />
      <path d="M9 9h6v12H9z" />
    </svg>
  );
}

function KpiIconPulse({ className, strokeWidth = 2 }: KpiIconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} className={className} aria-hidden>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

function KpiIconAlert({ className, strokeWidth = 2 }: KpiIconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} className={className} aria-hidden>
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function KpiIconTarget({ className, strokeWidth = 2 }: KpiIconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} className={className} aria-hidden>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  );
}

export function DashboardHomeClient() {
  // Each KPI value renders as a string so the placeholder "-" can sit
  // in place until /api/v1/stats resolves. We never read these strings
  // when statsLoading is true (the grid renders KpiCardSkeleton then),
  // but they remain initialized for the data-render branch.
  const [totalBds, setTotalBds] = useState<string>("-");
  const [newBds, setNewBds] = useState<string>("-");
  const [deficiencyAlerts, setDeficiencyAlerts] = useState<string>("-");
  const [highValueLeads, setHighValueLeads] = useState<string>("-");
  const [distribution, setDistribution] = useState<ClearingDistributionResponse["items"]>([]);
  const [alerts, setAlerts] = useState<AlertListItem[]>([]);

  // Per-source state slices so each tile can render its own
  // loading / error / data state and retry independently. Replaces
  // the previous single pageLoading gate that blocked all chrome
  // until the slowest source resolved.
  const [statsLoading, setStatsLoading] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [statsReloadKey, setStatsReloadKey] = useState(0);

  const [distributionLoading, setDistributionLoading] = useState(true);
  const [distributionError, setDistributionError] = useState<string | null>(null);
  const [distributionReloadKey, setDistributionReloadKey] = useState(0);

  const [alertsLoading, setAlertsLoading] = useState(true);
  // Two distinct error paths for /api/v1/alerts:
  //   alertsLoadError    — initial fetch failure → external retry block
  //   alertsActionError  — mark-read PATCH failure → AlertFeedCard's
  //                        existing inline banner
  // Splitting them avoids regressing the mark-read UX when the tile
  // is in a loaded state and a per-action failure occurs.
  const [alertsLoadError, setAlertsLoadError] = useState<string | null>(null);
  const [alertsActionError, setAlertsActionError] = useState<string | null>(null);
  const [alertsReloadKey, setAlertsReloadKey] = useState(0);

  const handleStatsRetry = useCallback(() => {
    setStatsReloadKey((k) => k + 1);
  }, []);
  const handleDistributionRetry = useCallback(() => {
    setDistributionReloadKey((k) => k + 1);
  }, []);
  const handleAlertsRetry = useCallback(() => {
    setAlertsReloadKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let active = true;
    setStatsLoading(true);
    setStatsError(null);

    apiRequest<DashboardStats>("/api/v1/stats")
      .then((stats) => {
        if (!active) return;
        setTotalBds(stats.total_active_bds.toLocaleString());
        setNewBds(stats.new_bds_30_days.toLocaleString());
        setDeficiencyAlerts(stats.deficiency_alerts.toLocaleString());
        setHighValueLeads(stats.high_value_leads.toLocaleString());
      })
      .catch((err) => {
        if (!active) return;
        setStatsError(err instanceof Error ? err.message : "Unable to load dashboard stats.");
      })
      .finally(() => {
        if (active) setStatsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [statsReloadKey]);

  useEffect(() => {
    let active = true;
    setDistributionLoading(true);
    setDistributionError(null);

    apiRequest<ClearingDistributionResponse>("/api/v1/stats/clearing-distribution")
      .then((resp) => {
        if (!active) return;
        setDistribution(resp.items);
      })
      .catch((err) => {
        if (!active) return;
        setDistributionError(
          err instanceof Error ? err.message : "Unable to load clearing distribution."
        );
      })
      .finally(() => {
        if (active) setDistributionLoading(false);
      });

    return () => {
      active = false;
    };
  }, [distributionReloadKey]);

  useEffect(() => {
    let active = true;
    setAlertsLoading(true);
    setAlertsLoadError(null);
    setAlertsActionError(null);

    apiRequest<AlertListResponse>("/api/v1/alerts?page=1&limit=6")
      .then((resp) => {
        if (!active) return;
        setAlerts(resp.items);
      })
      .catch((err) => {
        if (!active) return;
        setAlertsLoadError(err instanceof Error ? err.message : "Unable to load alerts.");
      })
      .finally(() => {
        if (active) setAlertsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [alertsReloadKey]);

  return (
    // App shell <main> owns the canvas bg. Typography is now applied at
    // the body level via `.dashboard-theme body {}` in globals.css — so
    // the sidebar (which lives outside this wrapper) also inherits Inter
    // + 14px + 1.5 line-height + antialiased.
    // Mockup uses 28px top / 36px horizontal / 48px bottom padding.
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* Topbar — crumbs + title LEFT, TopActions RIGHT on the same row.
          Mockup .topbar: display:flex; align-items:center; gap:16px; margin-bottom:28px. */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          {/* .crumbs: 12px, text-muted (slate-400), uppercase, 0.06em tracking.
              Only the "/" separator is in a span with text-dim (slate-600). */}
          <p className="text-[12px] uppercase tracking-[0.06em] text-slate-400">
            Enterprise Dashboard <span className="text-slate-600">/</span> Lead Intelligence
          </p>
          {/* .page-title: font-size 24px, weight 700, tracking -0.02em,
              margin-top 4px. No line-height → inherits body 1.5 (36px line-box).
              Using text-[24px] instead of text-2xl because text-2xl also
              applies line-height: 32px which shrinks the visible gap. */}
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-slate-900">
            Lead Intelligence Workspace
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      {/* KPI grid — branches on stats state:
          - statsError → full-width DashboardErrorCard with Retry
          - statsLoading → 4× KpiCardSkeleton mirroring real card geometry
          - data → 4× KpiCard (existing render) */}
      {statsError ? (
        <div className="mb-6">
          <DashboardErrorCard
            title="Couldn&rsquo;t load dashboard stats"
            message={statsError}
            onRetry={handleStatsRetry}
          />
        </div>
      ) : statsLoading ? (
        <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="animate-fade-in"><KpiCardSkeleton /></div>
          <div className="animate-fade-in delay-75"><KpiCardSkeleton /></div>
          <div className="animate-fade-in delay-150"><KpiCardSkeleton /></div>
          <div className="animate-fade-in delay-200"><KpiCardSkeleton /></div>
        </div>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="animate-fade-in">
            <KpiCard
              title="Total Active BDs"
              value={totalBds}
              tone="blue"
              icon={KpiIconBuilding}
              helper="All broker-dealers in Master List"
              href="/master-list?list=all"
              trend={{ direction: "up", label: "2.4%" }}
            />
          </div>
          <div className="animate-fade-in delay-75">
            <KpiCard
              title="New BDs · 30 days"
              value={newBds}
              tone="purple"
              icon={KpiIconPulse}
              helper="Recent registrations from filing activity"
              href="/master-list?list=all"
              trend={{ direction: "down", label: "66%" }}
            />
          </div>
          <div className="animate-fade-in delay-150">
            <KpiCard
              title="Deficiency Alerts"
              value={deficiencyAlerts}
              tone="red"
              icon={KpiIconAlert}
              helper="Active Form 17a-11 notices"
              href="/alerts?form_type=Form%2017a-11"
              trend={{ direction: "up", label: "12" }}
            />
          </div>
          <div className="animate-fade-in delay-200">
            <KpiCard
              title="High-Value Leads"
              value={highValueLeads}
              tone="amber"
              icon={KpiIconTarget}
              helper="Weighted scoring, last updated 8m ago"
              href="/master-list?lead_priority=hot"
              trend={{ direction: "up", label: "5" }}
            />
          </div>
        </div>
      )}

      {/* Trend (LEFT, narrower) + top leads (RIGHT, wider) — matches mockup 1fr 1.4fr.
          `h-full` on both animate wrappers forwards the grid row's stretched
          height to the cards inside, so the trend chart's flex-fill resolves
          to the actual row height instead of collapsing to the SVG's intrinsic
          220px baseline. */}
      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_1.4fr]">
        <div className="h-full animate-fade-in-left delay-300">
          <LeadVolumeTrendCard />
        </div>
        <div className="h-full animate-fade-in-right delay-300">
          <TopLeadsCard />
        </div>
      </div>

      {/* Provider distribution (LEFT, wider) + activity feed (RIGHT, narrower) — 1.4fr 1fr */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1.4fr_1fr]">
        <div className="animate-fade-in-left delay-[400ms]">
          <ClearingDistributionChart
            items={distribution}
            loading={distributionLoading}
            error={distributionError}
            onRetry={handleDistributionRetry}
          />
        </div>
        <div className="animate-fade-in-right delay-[400ms]">
          {alertsLoadError ? (
            // External retry block when the initial /api/v1/alerts fetch
            // fails. AlertFeedCard lives under frontend/components/alerts/**
            // (off-limits this PR), so we render the medallion error card
            // alongside it instead of modifying the AlertFeedCard signature.
            // Wrap in the same surface chrome AlertFeedCard uses so the
            // tile slot keeps a stable visual footprint.
            <article className="rounded-2xl border border-slate-200/70 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05)]">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-slate-900">Activity feed</h2>
                  <p className="mt-0.5 text-xs text-slate-500">Recent filing alerts</p>
                </div>
              </div>
              <DashboardErrorCard
                title="Couldn&rsquo;t load alerts"
                message={alertsLoadError}
                onRetry={handleAlertsRetry}
              />
            </article>
          ) : (
            <AlertFeedCard
              alerts={alerts}
              loading={alertsLoading}
              error={alertsActionError}
              onMarkRead={(alertId) => {
                setAlerts((current) =>
                  current.map((item) => (item.id === alertId ? { ...item, is_read: true } : item))
                );
                void apiRequest(`/api/v1/alerts/${alertId}/read`, { method: "PATCH" }).catch((markError) => {
                  setAlertsActionError(
                    markError instanceof Error ? markError.message : "Unable to update alert state."
                  );
                });
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
