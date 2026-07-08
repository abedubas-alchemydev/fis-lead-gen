import { ShieldAlert } from "lucide-react";

import { SharedLinksClient } from "@/components/shares/shared-links-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

// Admin surface for the DOX Share feature: every password-protected public
// share link exported from a favorite list, with rotate / revoke / delete
// lifecycle controls. The BE re-checks the admin role on every /shares
// call, so this gate is UX, not the security boundary.
export default async function SettingsSharedLinksPage() {
  const session = await getRequiredSession();

  if (session.user.role !== "admin") {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <section className="rounded-2xl border border-amber-500/25 bg-amber-500/12 p-8 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
          <div className="flex items-start gap-4">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface,#ffffff)] text-amber-600">
              <ShieldAlert className="h-5 w-5" aria-hidden />
            </div>
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-amber-700">Admin Only</p>
              <h1 className="text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
                Shared links are restricted
              </h1>
              <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                Only administrators can create and manage public share links.
                Viewer accounts can navigate the workspace but cannot modify
                account state.
              </p>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      {/* Topbar */}
      <div className="mb-7">
        <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
          Settings <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
          Shared Links
        </p>
        <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          Password-protected share links
        </h1>
      </div>

      <SharedLinksClient />
    </div>
  );
}
