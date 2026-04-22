"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, Building2, Target } from "lucide-react";

import { AlertFeedCard } from "@/components/alerts/alert-feed-card";
import { ClearingDistributionChart } from "@/components/dashboard/clearing-distribution-chart";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { LeadVolumeTrendCard } from "@/components/dashboard/lead-volume-trend-card";
import { TopLeadsCard } from "@/components/dashboard/top-leads-card";
import { apiRequest } from "@/lib/api";
import type { AlertListItem, AlertListResponse, ClearingDistributionResponse, DashboardStats } from "@/lib/types";

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

  const todayLabel = new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  return (
    // Full-bleed light-blue canvas — cancels app-shell main padding so the
    // dashboard owns its own surface (matches the mockup's body bg).
    <div className="-m-5 min-h-full bg-[#eaf3ff] px-7 py-7 lg:-m-6 lg:px-9 lg:py-7 xl:-m-7 xl:px-9">
      {/* Top crumbs + title */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
            Enterprise Dashboard <span className="text-slate-400">/</span> Lead Intelligence
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-[-0.02em] text-slate-900">
            Lead Intelligence Workspace
          </h1>
        </div>
        <p className="text-xs tabular-nums text-slate-500">{todayLabel}</p>
      </div>

      {/* KPI grid — exact mockup palette + sparklines */}
      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div className="animate-fade-in">
          <KpiCard
            title="Total Active BDs"
            value={totalBds}
            tone="blue"
            icon={Building2}
            helper={error ? "Backend data unavailable" : "All broker-dealers in Master List"}
            href="/master-list?list=all"
          />
        </div>
        <div className="animate-fade-in delay-75">
          <KpiCard
            title="New BDs · 30 days"
            value={newBds}
            tone="purple"
            icon={Activity}
            helper="Recent registrations from filing activity"
            href="/master-list?list=all"
          />
        </div>
        <div className="animate-fade-in delay-150">
          <KpiCard
            title="Deficiency Alerts"
            value={deficiencyAlerts}
            tone="red"
            icon={AlertTriangle}
            helper="Active Form 17a-11 notices"
            href="/alerts?form_type=Form%2017a-11"
          />
        </div>
        <div className="animate-fade-in delay-200">
          <KpiCard
            title="High-Value Leads"
            value={highValueLeads}
            tone="amber"
            icon={Target}
            helper="Weighted scoring"
            href="/master-list?lead_priority=hot"
          />
        </div>
      </div>

      {error ? (
        <div className="mb-6 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {/* Trend + top leads */}
      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_1.4fr]">
        <div className="animate-fade-in-left delay-300 xl:order-2">
          <LeadVolumeTrendCard />
        </div>
        <div className="animate-fade-in-right delay-300 xl:order-1">
          <TopLeadsCard />
        </div>
      </div>

      {/* Provider distribution + activity feed */}
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
