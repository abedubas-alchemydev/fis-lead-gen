import { ShieldAlert } from "lucide-react";

import { ClearingMembershipsAdminClient } from "@/components/settings/clearing-memberships-admin-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function ClearingMembershipsAdminPage() {
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
                Reputable-dispute membership review is restricted
              </h1>
              <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                The reputable-dispute directory importer routes ambiguous name matches to a review
                queue. Only administrators can approve or reject those candidates.
              </p>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <ClearingMembershipsAdminClient />
    </div>
  );
}
