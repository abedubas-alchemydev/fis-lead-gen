/**
 * Skeleton shown while a firm-detail page (BD or IA) is fetching its
 * profile payload. Used in two spots:
 *   - Next.js route-level loading.tsx files (Suspense fallback during navigation)
 *   - The in-component `!profile` fallback inside the *DetailClient components
 * Keeping them in one component prevents a flash when the Suspense fallback
 * unmounts and the in-component fallback takes over.
 *
 * Skeletons are preferred over a centered spinner here because the layout is
 * known (header + 2-col card grid) — previewing structure reduces perceived
 * wait time and avoids the "is it stuck?" feeling.
 */
export function DetailPageSkeleton() {
  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div
        className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
      >
        <div className="h-6 w-56 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
        <div className="mt-4 h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
        <div className="mt-8 grid gap-4 xl:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
