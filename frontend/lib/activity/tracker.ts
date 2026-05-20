"use client";

// Client-side activity event queue + flush. Mirrors the security event
// reporter (frontend/components/security/report-event.ts) but with
// batching since activity events fan out 10–100× faster than security
// shield events. The server side lives at app/api/activity/events/route.ts
// and writes scrubbed rows into the user_activity table.
//
// Callers don't sample — every enqueue counts. The queue caps at 50,
// drops oldest on overflow so a sudden burst can't blow memory.

export type ActivityAction =
  | "nav_view"
  | "nav_click"
  | "link_open"
  | "search_query"
  | "input_used";

export interface ActivityMeta {
  nav_label?: string;
  from_path?: string;
  to_path?: string;
  host?: string;
  target?: string;
  filter_keys?: string[];
  query_present?: boolean;
  query_length?: number;
  field_name?: string;
  field_id?: string;
  field_length?: number;
  field_populated?: boolean;
  form_id?: string;
  button_label?: string;
}

interface QueuedEvent {
  action: ActivityAction;
  path: string | null;
  meta: ActivityMeta | null;
}

const MAX_QUEUE = 50;
const FLUSH_INTERVAL_MS = 5_000;
const ENDPOINT = "/api/activity/events";

let queue: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let unloadListenersAttached = false;

function attachUnloadListeners(): void {
  if (unloadListenersAttached) return;
  if (typeof document === "undefined") return;
  unloadListenersAttached = true;
  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.visibilityState === "hidden") flushNow();
    },
    { capture: false },
  );
  window.addEventListener("pagehide", () => flushNow(), { capture: false });
}

function armFlushTimer(): void {
  if (flushTimer !== null) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushNow();
  }, FLUSH_INTERVAL_MS);
}

export function enqueueActivity(
  action: ActivityAction,
  path: string | null,
  meta: ActivityMeta | null,
): void {
  if (typeof window === "undefined") return;
  attachUnloadListeners();
  // Drop-oldest on overflow. Recent events are more useful than stale
  // ones — sustained overflow means we'd already be over the server
  // rate limit so the loss is bounded either way.
  if (queue.length >= MAX_QUEUE) queue.shift();
  queue.push({ action, path, meta });
  armFlushTimer();
}

export function flushNow(): void {
  if (typeof window === "undefined") return;
  if (queue.length === 0) return;
  const batch = queue;
  queue = [];
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }

  const body = JSON.stringify({ events: batch });

  // sendBeacon is the right primitive for unload-safe flushing — fire-
  // and-forget, survives navigation, doesn't show in the network panel
  // as pending. Fall back to fetch + keepalive when sendBeacon is
  // missing or rejects (some browsers cap the payload size).
  try {
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" });
      const ok = navigator.sendBeacon(ENDPOINT, blob);
      if (ok) return;
    }
  } catch {
    // fall through to fetch
  }

  try {
    void fetch(ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      keepalive: true,
      credentials: "same-origin",
    });
  } catch {
    // Best-effort logging — never throw from the activity path.
  }
}

// Test seam: clear in-memory state. Not exported from a barrel — only
// the unit test reaches into it.
export function __resetForTests(): void {
  queue = [];
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
}
