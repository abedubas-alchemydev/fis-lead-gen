"use client";

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";

import { apiRequest } from "@/lib/api";

interface ActiveRefreshResponse {
  is_active: boolean;
  started_at: string | null;
}

const POLL_INTERVAL_MS = 30_000;
const DISMISS_STORAGE_KEY = "dashboard-refresh-banner-dismissed";

// Friendly client-facing copy. Avoid jargon ("Gemini extraction",
// "pipeline run"); avoid alarming framing ("warning", "outage").
// Re-read the broader plan: clients see this banner when any
// USER_FACING_REFRESH_PIPELINES parent run is in flight.
const BANNER_COPY =
  "Refreshing your records to keep them up to date — fresh data is on the way.";

/**
 * Banner shown at the top of /dashboard while any user-visible refresh
 * pipeline is running. Polls /api/v1/pipeline/active-refreshes every 30 s.
 * Dismissible per browser tab session via sessionStorage; a full page
 * reload re-arms it.
 */
export function DataRefreshBanner() {
  const [isActive, setIsActive] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);

  // Restore dismissed state on mount. Wrapped in a useEffect so SSR
  // doesn't try to touch window.sessionStorage during render.
  useEffect(() => {
    try {
      if (window.sessionStorage.getItem(DISMISS_STORAGE_KEY) === "1") {
        setIsDismissed(true);
      }
    } catch {
      /* sessionStorage may throw in some sandboxed contexts (e.g. tracking
       * prevention). Banner just stays armed in that case — better than
       * silently hiding. */
    }
  }, []);

  // Polling effect: mirror the setTimeout-based pattern used by
  // broker-dealer-detail-client.tsx so we don't introduce a new
  // useInterval hook just for this one banner.
  useEffect(() => {
    let active = true;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const result = await apiRequest<ActiveRefreshResponse>(
          "/api/v1/pipeline/active-refreshes",
        );
        if (!active) return;
        setIsActive(result.is_active);
      } catch {
        /* Transient errors (network blip, 401 during session expiry) —
         * keep the banner in its last-known state rather than flashing
         * off-then-on. Next poll will resolve. */
      }
      if (active) {
        pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    void poll();

    return () => {
      active = false;
      if (pollTimer !== null) clearTimeout(pollTimer);
    };
  }, []);

  const handleDismiss = useCallback(() => {
    setIsDismissed(true);
    try {
      window.sessionStorage.setItem(DISMISS_STORAGE_KEY, "1");
    } catch {
      /* See note above — non-fatal. */
    }
  }, []);

  if (!isActive || isDismissed) {
    return null;
  }

  return (
    <div
      className="data-refresh-banner relative mb-4 flex items-center gap-3 overflow-hidden rounded-2xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-[13px] text-[var(--text-dim,#475569)]"
      role="status"
      aria-live="polite"
    >
      {/* Shimmer overlay — animated horizontal gradient sweep. Pure CSS so
       * it stays smooth on low-end clients. Respects prefers-reduced-motion. */}
      <span className="data-refresh-banner__shimmer pointer-events-none absolute inset-0" aria-hidden />

      {/* Pulsing dot — same idiom as RefreshingIndicator so visual
       * language is consistent across the app. */}
      <span className="relative inline-flex h-2 w-2 shrink-0" aria-hidden>
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent,#6366f1)] opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--accent,#6366f1)]" />
      </span>

      <span className="relative flex-1 font-medium text-[var(--text,#0f172a)]">
        {BANNER_COPY}
      </span>

      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss data refresh notice"
        className="relative inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
      >
        <X className="h-4 w-4" strokeWidth={2} aria-hidden />
      </button>

      <style jsx>{`
        .data-refresh-banner__shimmer {
          background: linear-gradient(
            90deg,
            transparent 0%,
            rgba(99, 102, 241, 0.08) 50%,
            transparent 100%
          );
          background-size: 200% 100%;
          background-repeat: no-repeat;
          background-position: -100% 0;
          animation: data-refresh-banner-shimmer 3s linear infinite;
        }
        @keyframes data-refresh-banner-shimmer {
          0% {
            background-position: -100% 0;
          }
          100% {
            background-position: 200% 0;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .data-refresh-banner__shimmer {
            animation: none;
            background: none;
          }
        }
      `}</style>
    </div>
  );
}
