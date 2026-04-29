import { ShieldAlert } from "lucide-react";

import { PipelinesAdminClient } from "@/components/settings/pipelines/pipelines-admin-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function SettingsPipelinesPage() {
  const session = await getRequiredSession();

  if (session.user.role !== "admin") {
    return (
      <section className="rounded-[30px] border border-amber-200 bg-amber-50 p-8 shadow-shell">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-white p-3 text-warning">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium uppercase tracking-[0.24em] text-warning">
              Admin Only
            </p>
            <h1 className="text-2xl font-semibold text-navy">
              Pipeline triggers are restricted
            </h1>
            <p className="max-w-2xl text-sm leading-6 text-slate-700">
              Only administrators can kick off the Tier 2 pipelines. Reach
              out to an admin if you need an ad-hoc refresh.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return <PipelinesAdminClient />;
}
