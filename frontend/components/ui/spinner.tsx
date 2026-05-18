/**
 * Full-page loading spinner shown during page transitions.
 * Used inside Next.js loading.tsx files so it displays automatically
 * when navigating between routes.
 */
export function PageSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-5">
      <div className="relative h-12 w-12">
        {/* Outer ring */}
        <div className="absolute inset-0 rounded-full border-4 border-[var(--border,rgba(30,64,175,0.1))]" />
        {/* Spinning arc */}
        <div className="absolute inset-0 animate-spin rounded-full border-4 border-transparent border-t-[var(--accent,#6366f1)]" />
      </div>
      <p className="text-sm font-medium tracking-wide text-[var(--text-muted,#94a3b8)]">{label}</p>
    </div>
  );
}
