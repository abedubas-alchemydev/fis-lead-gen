/**
 * Non-blocking "refreshing" pill shown in firm-detail topbars while the
 * refresh-on-visit orchestrator is running. The page renders normally
 * with whatever data the DB has now (stale-while-revalidate); this pill
 * tells the user fresh data is in flight without blocking the view.
 *
 * Used by both BD and IA detail clients. `label` defaults to a generic
 * "Refreshing data…" but callers pass a pipeline-specific label when the
 * orchestrator's polling phase reports which sub-pipelines are running.
 */
export function RefreshingIndicator({ label = "Refreshing data…" }: { label?: string }) {
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1 text-[11px] font-medium text-[var(--text-dim,#475569)]"
      role="status"
      aria-live="polite"
    >
      <span className="relative inline-flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent,#6366f1)] opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--accent,#6366f1)]" />
      </span>
      {label}
    </span>
  );
}
