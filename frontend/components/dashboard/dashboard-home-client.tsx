"use client";

import { useEffect, useState } from "react";

import { AlertFeedCard } from "@/components/alerts/alert-feed-card";
import { ClearingDistributionChart } from "@/components/dashboard/clearing-distribution-chart";
import { KpiCard } from "@/components/dashboard/kpi-card";
import type { KpiIconProps } from "@/components/dashboard/kpi-card";
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
  const [totalBds, setTotalBds] = useState<string>("-");
  const [newBds, setNewBds] = useState<string>("-");
  const [deficiencyAlerts, setDeficiencyAlerts] = useState<string>("-");
  const [highValueLeads, setHighValueLeads] = useState<string>("-");
  const [distribution, setDistribution] = useState<ClearingDistributionResponse["items"]>([]);
  const [alerts, setAlerts] = useState<AlertListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadStats() {
      const [statsResult, distributionResult, alertsResult] = await Promise.allSettled([
        apiRequest<DashboardStats>("/api/v1/stats"),
        apiRequest<ClearingDistributionResponse>("/api/v1/stats/clearing-distribution"),
        apiRequest<AlertListResponse>("/api/v1/alerts?page=1&limit=6")
      ]);

      if (!active) return;

      if (statsResult.status === "fulfilled") {
        setTotalBds(statsResult.value.total_active_bds.toLocaleString());
        setNewBds(statsResult.value.new_bds_30_days.toLocaleString());
        setDeficiencyAlerts(statsResult.value.deficiency_alerts.toLocaleString());
        setHighValueLeads(statsResult.value.high_value_leads.toLocaleString());
      } else {
        const message = statsResult.reason instanceof Error ? statsResult.reason.message : "Unable to load dashboard stats.";
        setError(message);
      }

      if (distributionResult.status === "fulfilled") {
        setDistribution(distributionResult.value.items);
      }

      if (alertsResult.status === "fulfilled") {
        setAlerts(alertsResult.value.items);
      } else {
        const message = alertsResult.reason instanceof Error ? alertsResult.reason.message : "Unable to load alerts.";
        setAlertsError(message);
      }

      setAlertsLoading(false);
      setPageLoading(false);
    }

    void loadStats();

    return () => {
      active = false;
    };
  }, []);

  if (pageLoading) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-5">
        <div className="relative h-12 w-12">
          <div className="absolute inset-0 rounded-full border-4 border-slate-200" />
          <div className="absolute inset-0 animate-spin rounded-full border-4 border-transparent border-t-navy" />
        </div>
        <p className="text-sm font-medium tracking-wide text-slate-500">Loading dashboard</p>
      </div>
    );
  }

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

      {/* KPI grid */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="animate-fade-in">
          <KpiCard
            title="Total Active BDs"
            value={totalBds}
            tone="blue"
            icon={KpiIconBuilding}
            helper={error ? "Backend data unavailable" : "All broker-dealers in Master List"}
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

      {error ? (
        <div className="mb-6 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

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
          <ClearingDistributionChart items={distribution} />
        </div>
        <div className="animate-fade-in-right delay-[400ms]">
          <AlertFeedCard
            alerts={alerts}
            loading={alertsLoading}
            error={alertsError}
            onMarkRead={(alertId) => {
              setAlerts((current) =>
                current.map((item) => (item.id === alertId ? { ...item, is_read: true } : item))
              );
              void apiRequest(`/api/v1/alerts/${alertId}/read`, { method: "PATCH" }).catch((markError) => {
                setAlertsError(
                  markError instanceof Error ? markError.message : "Unable to update alert state."
                );
              });
            }}
          />
        </div>
      </div>
    </div>
  );
}
