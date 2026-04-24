import { FavoritesClient } from "@/components/favorites/favorites-client";
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
            Firms you&apos;ve starred, newest first. Unfavorite from a row when you&apos;re done with it.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <FavoritesClient />
    </div>
  );
}
