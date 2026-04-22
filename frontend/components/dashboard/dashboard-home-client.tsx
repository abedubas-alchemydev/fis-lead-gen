"use client";

import { useEffect, useState } from "react";
import { Activity, AlertTriangle, Building2, Target } from "lucide-react";

import { AlertFeedCard } from "@/components/alerts/alert-feed-card";
import { ClearingDistributionChart } from "@/components/dashboard/clearing-distribution-chart";
import { KpiCard } from "@/components/dashboard/kpi-card";
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
    <div className="relative space-y-10">
      {/* Decorative ambient orbs behind the dashboard content. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -left-16 -top-20 h-64 w-64 rounded-full bg-blue/10 blur-3xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute right-0 top-40 h-72 w-72 rounded-full bg-gold/10 blur-3xl"
      />

      {/* Page header */}
      <header className="relative animate-fade-in">
        <p className="text-xs font-medium uppercase tracking-[0.3em] text-blue">Overview</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
          <h1 className="text-3xl font-semibold leading-tight text-navy sm:text-4xl">
            Lead intelligence at a glance
          </h1>
          <p className="text-sm text-slate-500">{todayLabel}</p>
        </div>
        <div className="mt-4 h-px w-full bg-gradient-to-r from-slate-200 via-slate-200/40 to-transparent" />
      </header>

      {/* Stats grid */}
      <section className="relative space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-[0.24em] text-slate-500">Key metrics</p>
        </div>
        <div className="grid gap-4 xl:grid-cols-4">
          <div className="animate-fade-in">
            <KpiCard
              title="Total Active BDs"
              value={totalBds}
              tone="navy"
              icon={Building2}
              helper={error ? "Backend data unavailable" : "View all broker-dealers in the Master List"}
              href="/master-list?list=all"
            />
          </div>
          <div className="animate-fade-in delay-75">
            <KpiCard
              title="New BDs (30 days)"
              value={newBds}
              tone="blue"
              icon={Activity}
              helper="Recent broker-dealer registrations from filing activity"
              href="/master-list?list=all"
            />
          </div>
          <div className="animate-fade-in delay-150">
            <KpiCard
              title="Deficiency Alerts"
              value={deficiencyAlerts}
              tone="danger"
              icon={AlertTriangle}
              helper="Active Form 17a-11 notices"
              href="/alerts?form_type=Form%2017a-11"
            />
          </div>
          <div className="animate-fade-in delay-200">
            <KpiCard
              title="High-Value Leads"
              value={highValueLeads}
              tone="gold"
              icon={Target}
              helper="Hot leads based on weighted scoring"
              href="/master-list?lead_priority=hot"
            />
          </div>
        </div>
      </section>

      {error ? (
        <div className="relative rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      {/* Activity + market */}
      <section className="relative space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-[0.24em] text-slate-500">
            Activity &amp; market
          </p>
        </div>
        <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
          <div className="animate-fade-in-left delay-300">
            <AlertFeedCard
              alerts={alerts}
              loading={alertsLoading}
              error={alertsError}
              onMarkRead={(alertId) => {
                setAlerts((current) => current.map((item) => (item.id === alertId ? { ...item, is_read: true } : item)));
                void apiRequest(`/api/v1/alerts/${alertId}/read`, { method: "PATCH" }).catch((markError) => {
                  setAlertsError(markError instanceof Error ? markError.message : "Unable to update alert state.");
                });
              }}
            />
          </div>
          <div className="animate-fade-in-right delay-300">
            <ClearingDistributionChart items={distribution} />
          </div>
        </div>
      </section>
    </div>
  );
}
