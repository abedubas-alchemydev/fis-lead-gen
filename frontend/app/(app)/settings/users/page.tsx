import { ShieldAlert } from "lucide-react";

import { UsersAdminClient } from "@/components/settings/users-admin-client";
import { db } from "@/lib/auth";
import { getRequiredSession } from "@/lib/auth-server";

type PendingUserRow = {
  id: string;
  email: string;
  name: string;
  created_at: Date;
  email_verified: boolean;
};

export const dynamic = "force-dynamic";

export default async function SettingsUsersPage() {
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
                User approvals are restricted
              </h1>
              <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                Only administrators can approve or reject pending signups. Viewer accounts can
                navigate the workspace but cannot modify account state.
              </p>
            </div>
          </div>
        </section>
      </div>
    );
  }

  const result = await db.query<PendingUserRow>(
    'SELECT id, email, name, created_at, email_verified FROM "user" WHERE status = $1 ORDER BY created_at ASC LIMIT 50',
    ["pending"]
  );

  const pendingUsers = result.rows.map((r) => ({
    id: r.id,
    email: r.email,
    name: r.name,
    createdAt: r.created_at.toISOString(),
    emailVerified: r.email_verified,
  }));

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <UsersAdminClient
        pendingUsers={pendingUsers}
        currentAdminId={session.user.id}
      />
    </div>
  );
}
