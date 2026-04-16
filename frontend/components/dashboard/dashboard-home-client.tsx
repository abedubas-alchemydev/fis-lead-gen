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

  useEffect(() => {
    let active = true;

    async function loadStats() {
      try {
        const [response, clearingDistribution, alertResponse] = await Promise.all([
          apiRequest<DashboardStats>("/api/v1/stats"),
          apiRequest<ClearingDistributionResponse>("/api/v1/stats/clearing-distribution"),
          apiRequest<AlertListResponse>("/api/v1/alerts?page=1&limit=6")
        ]);
        if (active) {
          setTotalBds(response.total_active_bds.toLocaleString());
          setNewBds(response.new_bds_30_days.toLocaleString());
          setDeficiencyAlerts(response.deficiency_alerts.toLocaleString());
          setHighValueLeads(response.high_value_leads.toLocaleString());
          setDistribution(clearingDistribution.items);
          setAlerts(alertResponse.items);
        }
      } catch (loadError) {
        if (active) {
          const message = loadError instanceof Error ? loadError.message : "Unable to load broker-dealer counts.";
          setError(message);
          setAlertsError(message);
        }
      } finally {
        if (active) {
          setAlertsLoading(false);
        }
      }
    }

    void loadStats();

    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-8">
      <div className="grid gap-4 xl:grid-cols-4">
        <KpiCard
          title="Total Active BDs"
          value={totalBds}
          tone="navy"
          icon={Building2}
          helper={error ? "Backend data unavailable" : "Live broker-dealer count from the platform database"}
        />
        <KpiCard title="New BDs (30 days)" value={newBds} tone="blue" icon={Activity} helper="Recent broker-dealer registrations from filing activity" />
        <KpiCard
          title="Deficiency Alerts"
          value={deficiencyAlerts}
          tone="danger"
          icon={AlertTriangle}
          helper="Active Form 17a-11 notices"
          href="/alerts?form_type=Form%2017a-11"
        />
        <KpiCard
          title="High-Value Leads"
          value={highValueLeads}
          tone="gold"
          icon={Target}
          helper="Hot leads based on weighted scoring"
          href="/master-list?lead_priority=hot"
        />
      </div>

      {error ? (
        <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">{error}</div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
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
        <ClearingDistributionChart items={distribution} />
      </div>
    </div>
  );
}
