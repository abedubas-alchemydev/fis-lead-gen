import { ShieldAlert } from "lucide-react";

import { PipelineAdminClient } from "@/components/settings/pipeline-admin-client";
import { getRequiredSession } from "@/lib/auth-server";

export default async function SettingsPage() {
  const session = await getRequiredSession();

  if (session.user.role !== "admin") {
    return (
      <section className="rounded-2xl border border-amber-500/25 bg-amber-500/12 p-8 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
        <div className="flex items-start gap-4">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface,#ffffff)] text-amber-600">
            <ShieldAlert className="h-5 w-5" aria-hidden />
          </div>
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-amber-700">Admin Only</p>
            <h1 className="text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
              Settings are restricted
            </h1>
            <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
              Global configuration is reserved for administrators. Viewer accounts can access the
              platform and navigate the workspace, but they cannot modify system settings.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return <PipelineAdminClient />;
}
