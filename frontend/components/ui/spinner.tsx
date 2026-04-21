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
        <div className="absolute inset-0 rounded-full border-4 border-slate-200" />
        {/* Spinning arc */}
        <div className="absolute inset-0 animate-spin rounded-full border-4 border-transparent border-t-navy" />
      </div>
      <p className="text-sm font-medium tracking-wide text-slate-500">{label}</p>
    </div>
  );
}
