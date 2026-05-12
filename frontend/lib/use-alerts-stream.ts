"use client";

import { useEffect } from "react";

import type { AlertListItem } from "@/lib/types";

const STREAM_URL = "/api/backend/api/v1/alerts/stream";

export type AlertStreamPayload = {
  items: AlertListItem[];
};

/**
 * Subscribe to the SSE `/api/v1/alerts/stream` endpoint via EventSource.
 *
 * The browser handles reconnects automatically; on each reconnect it sends
 * the most recent `Last-Event-ID` header, and the backend replays any
 * alerts inserted while the connection was down. The caller's `onInsert`
 * is invoked with the hydrated AlertListItem batch; de-duplication by id
 * is the caller's responsibility (see DashboardHomeClient.handleInsert).
 *
 * When `enabled` is false (e.g. the initial REST fetch errored, so we
 * don't want to start streaming until the user retries) the EventSource
 * is never opened.
 */
export function useAlertsStream(
  onInsert: (alerts: AlertListItem[]) => void,
  enabled: boolean = true,
): void {
  useEffect(() => {
    if (!enabled || typeof window === "undefined") {
      return;
    }

    const source = new EventSource(STREAM_URL, { withCredentials: true });

    const handleInserted = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as AlertStreamPayload;
        if (Array.isArray(payload.items) && payload.items.length > 0) {
          onInsert(payload.items);
        }
      } catch {
        // Malformed frame; swallow. The next reconnect's Last-Event-ID
        // replay catches any missed data.
      }
    };

    source.addEventListener("alert.inserted", handleInserted);
    // EventSource auto-reconnects with backoff on `error`. Nothing to do
    // here unless we want to surface a UI state for "disconnected"; the
    // current dashboard tile has no such affordance, so leave it silent.

    return () => {
      source.removeEventListener("alert.inserted", handleInserted);
      source.close();
    };
  }, [enabled, onInsert]);
}
