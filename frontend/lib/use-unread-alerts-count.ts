"use client";

import { useEffect, useState } from "react";

import { apiRequest, buildApiPath } from "@/lib/api";
import type { AlertListResponse } from "@/lib/types";

// Unread-alert count shared by the sidebar Alerts badge (AppShell) and the
// master-list topbar bell pip. Both affordances were originally fed by the
// dashboard's ``stats.deficiency_alerts``; when that stat was retired with
// the "Pending Approval BDs" KPI swap they went dark, so this hook rewires
// them to the /alerts API itself: a one-shot ``limit=1`` probe (the same
// trick the alerts page uses for its tab-count badges) whose ``meta.total``
// is the number of unread alerts across the All Alerts view the page
// defaults to.
//
// One GET per mount, silent on failure — callers just render no badge/pip,
// which doubles as the graceful zero-state. Pass ``enabled: false`` to skip
// the fetch entirely (e.g. when the user lacks the ``alerts`` feature and
// the probe would only 403).
const UNREAD_ALERTS_COUNT_PATH = buildApiPath("/api/v1/alerts", {
  category: "all",
  read: false,
  limit: 1,
});

export function useUnreadAlertsCount(enabled: boolean = true): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let active = true;
    apiRequest<AlertListResponse>(UNREAD_ALERTS_COUNT_PATH)
      .then((response) => {
        if (active) setCount(response.meta.total);
      })
      .catch(() => {
        /* swallow — badge/pip just stay hidden */
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  return count;
}
