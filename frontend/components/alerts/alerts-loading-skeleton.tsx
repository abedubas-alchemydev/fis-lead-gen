"use client";

// Skeleton rows that mimic the alert-row shape (priority dot + pill
// row + title + summary lines + action chips) so the layout stays
// stable when the fetch resolves. Renders inside the same list card
// padding as the real rows.
//
// Uses inline animate-pulse Tailwind utilities — same pattern used
// elsewhere in this codebase. No dedicated <Skeleton /> primitive
// exists, and adding one is out of scope for this PR.
export function AlertsLoadingSkeleton() {
  return (
    <div aria-busy>
      {Array.from({ length: 6 }).map((_, index) => (
        <div
          key={`alerts-skel-${index}`}
          className="flex gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
        >
          <span
            aria-hidden
            className="mt-2 h-2 w-2 shrink-0 animate-pulse rounded-full bg-[var(--surface-3,#dbeafe)]"
          />
          <div className="min-w-0 flex-1">
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              <span className="h-5 w-[64px] animate-pulse rounded-full bg-[var(--surface-2,#f1f6fd)]" />
              <span className="h-5 w-[56px] animate-pulse rounded-full bg-[var(--surface-2,#f1f6fd)]" />
              <span className="ml-auto h-3 w-24 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
            </div>
            <div className="mb-2 h-3.5 w-2/5 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
            <div className="space-y-1.5">
              <div className="h-3 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              <div className="h-3 w-4/5 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="h-6 w-[78px] animate-pulse rounded-md bg-[var(--surface-2,#f1f6fd)]" />
              <span className="h-6 w-[72px] animate-pulse rounded-md bg-[var(--surface-2,#f1f6fd)]" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
