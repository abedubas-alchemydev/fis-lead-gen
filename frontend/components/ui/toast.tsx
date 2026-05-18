"use client";

import { AlertTriangle, Check, Info, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

// Visual primitive for a single toast row. Pure presentational — the
// auto-dismiss timer lives here so each row owns its own lifecycle and
// can pause on hover without leaking timers up to the toaster stack.

export type ToastVariant = "success" | "error" | "info";

export interface ToastProps {
  variant: ToastVariant;
  title?: string;
  body: string;
  durationMs?: number;
  onDismiss: () => void;
}

const VARIANT_STYLES: Record<
  ToastVariant,
  { container: string; icon: string; Icon: typeof Check }
> = {
  success: {
    container: "border-emerald-200 bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)]",
    icon: "text-emerald-500",
    Icon: Check,
  },
  error: {
    container: "border-red-200 bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)]",
    icon: "text-red-500",
    Icon: AlertTriangle,
  },
  info: {
    container: "border-sky-200 bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)]",
    icon: "text-sky-500",
    Icon: Info,
  },
};

export function Toast({ variant, title, body, durationMs = 5000, onDismiss }: ToastProps) {
  const [paused, setPaused] = useState(false);
  const remainingRef = useRef(durationMs);
  const startedAtRef = useRef<number>(Date.now());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onDismissRef = useRef(onDismiss);

  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);

  useEffect(() => {
    if (paused) {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      remainingRef.current -= Date.now() - startedAtRef.current;
      return;
    }

    startedAtRef.current = Date.now();
    timerRef.current = setTimeout(() => {
      onDismissRef.current();
    }, Math.max(remainingRef.current, 0));

    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [paused]);

  const styles = VARIANT_STYLES[variant];
  const Icon = styles.Icon;

  return (
    <div
      role="status"
      aria-live="polite"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className={`pointer-events-auto flex w-full items-start gap-3 rounded-2xl border px-4 py-3 shadow-lg shadow-black/10 ${styles.container}`}
    >
      <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${styles.icon}`} aria-hidden />
      <div className="min-w-0 flex-1 text-sm">
        {title ? <div className="font-semibold text-[var(--text,#0f172a)]">{title}</div> : null}
        <div className="leading-snug text-[var(--text-dim,#475569)]">{body}</div>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        className="-m-1 rounded-md p-1 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text-dim,#475569)] focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}
