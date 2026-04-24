import { Star } from "lucide-react";

import { TopActions } from "@/components/layout/top-actions";

export default function MyFavoritesPage() {
  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-slate-400">
            Workspace <span className="text-slate-600">/</span> My Favorites
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-slate-900">
            My Favorites
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Firms you&apos;ve starred will appear here.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <div className="flex min-h-[340px] items-center justify-center rounded-[30px] border border-white/80 bg-white/88 p-10 shadow-shell backdrop-blur">
        <div className="flex flex-col items-center text-center">
          <div className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-500">
            <Star className="h-6 w-6" strokeWidth={1.75} aria-hidden />
          </div>
          <h2 className="mt-5 text-lg font-semibold text-navy">Coming soon</h2>
          <p className="mt-2 max-w-sm text-sm text-slate-600">
            Starring firms and reviewing your shortlist will land in a follow-up release.
          </p>
        </div>
      </div>
    </div>
  );
}
