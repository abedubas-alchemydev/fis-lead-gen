import { ShieldAlert } from "lucide-react";

import { PipelineAdminClient } from "@/components/settings/pipeline-admin-client";
import { getRequiredSession } from "@/lib/auth-server";

export default async function SettingsPage() {
  const session = await getRequiredSession();

  if (session.user.role !== "admin") {
    return (
      <section className="rounded-[30px] border border-amber-200 bg-amber-50 p-8 shadow-shell">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-white p-3 text-warning">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium uppercase tracking-[0.24em] text-warning">Admin Only</p>
            <h1 className="text-2xl font-semibold text-navy">Settings are restricted</h1>
            <p className="max-w-2xl text-sm leading-6 text-slate-700">
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
