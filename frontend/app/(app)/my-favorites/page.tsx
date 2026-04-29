import { TopActions } from "@/components/layout/top-actions";
import { MyFavoritesClient } from "@/components/my-favorites/my-favorites-client";

export default function MyFavoritesPage() {
  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      {/* ── Topbar ───────────────────────────────────────────────────────── */}
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> My Favorites
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Saved firms
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <MyFavoritesClient />
    </div>
  );
}
