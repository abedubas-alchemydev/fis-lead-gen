"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";

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
      className="data-refresh-banner relative mb-5 flex items-center gap-4 overflow-hidden rounded-2xl border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.10)] px-5 py-4 text-[14px] text-[var(--text,#0f172a)] shadow-[0_1px_3px_rgba(15,23,42,0.04)]"
      role="status"
      aria-live="polite"
    >
      {/* Shimmer overlay — animated horizontal gradient sweep across the
       * banner background. Pure CSS so it stays smooth on low-end clients.
       * prefers-reduced-motion neutralises it below. */}
      <span className="data-refresh-banner__shimmer pointer-events-none absolute inset-0" aria-hidden />

      {/* Spinning refresh icon — visual cue for "data is moving" that's
       * recognisable at a glance. Sits in a soft accent circle so the
       * spin reads even on the tinted banner background. */}
      <span
        className="relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[rgba(99,102,241,0.18)] text-[var(--accent,#6366f1)]"
        aria-hidden
      >
        <RefreshCw
          className="data-refresh-banner__icon h-5 w-5"
          strokeWidth={2.25}
        />
      </span>

      <span className="relative flex-1 font-medium leading-snug">
        {BANNER_COPY}
      </span>

      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss data refresh notice"
        className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
      >
        <X className="h-4 w-4" strokeWidth={2} aria-hidden />
      </button>

      <style jsx>{`
        .data-refresh-banner {
          animation: data-refresh-banner-enter 320ms cubic-bezier(0.16, 1, 0.3, 1) both;
        }
        .data-refresh-banner__icon {
          animation: data-refresh-banner-spin 2.4s linear infinite;
          transform-origin: 50% 50%;
        }
        .data-refresh-banner__shimmer {
          background: linear-gradient(
            90deg,
            transparent 0%,
            rgba(99, 102, 241, 0.12) 50%,
            transparent 100%
          );
          background-size: 200% 100%;
          background-repeat: no-repeat;
          background-position: -100% 0;
          animation: data-refresh-banner-shimmer 3s linear infinite;
        }
        @keyframes data-refresh-banner-enter {
          from {
            opacity: 0;
            transform: translateY(-8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        @keyframes data-refresh-banner-spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
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
          .data-refresh-banner,
          .data-refresh-banner__icon,
          .data-refresh-banner__shimmer {
            animation: none;
          }
          .data-refresh-banner__shimmer {
            background: none;
          }
        }
      `}</style>
    </div>
  );
}
