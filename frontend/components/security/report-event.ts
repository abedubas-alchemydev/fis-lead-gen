"use client";

// Client-side dispatcher for security audit events. The server side lives at
// app/api/security/event/route.ts and writes into the existing audit_log
// table. Callers should already have sampled — this fires every call.

export type SecurityEventKind =
  | "copy_blocked"
  | "right_click"
  | "print_blocked"
  | "devtools_open"
  | "shortcut_blocked"
  | "clipboard_cleared";

export function reportSecurityEvent(
  kind: SecurityEventKind,
  meta?: Record<string, unknown>
): void {
  if (typeof window === "undefined") return;

  const body = JSON.stringify({ kind, meta: meta ?? null });

  // sendBeacon is the right primitive — it survives page unload, doesn't
  // block, and doesn't show up in the network panel as a pending request.
  // Fall back to fetch with keepalive when sendBeacon is missing or
  // rejects the payload (some browsers cap size or content-type).
  try {
    if (typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" });
      const ok = navigator.sendBeacon("/api/security/event", blob);
      if (ok) return;
    }
  } catch {
    // fall through to fetch
  }

  try {
    void fetch("/api/security/event", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      keepalive: true,
      credentials: "same-origin",
    });
  } catch {
    // Best-effort logging — never throw out of a security listener.
  }
}
