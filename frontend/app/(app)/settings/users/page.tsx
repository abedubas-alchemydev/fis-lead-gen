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
      <section className="rounded-[30px] border border-amber-200 bg-amber-50 p-8 shadow-shell">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-white p-3 text-warning">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium uppercase tracking-[0.24em] text-warning">Admin Only</p>
            <h1 className="text-2xl font-semibold text-navy">User approvals are restricted</h1>
            <p className="max-w-2xl text-sm leading-6 text-slate-700">
              Only administrators can approve or reject pending signups.
            </p>
          </div>
        </div>
      </section>
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
    <UsersAdminClient
      pendingUsers={pendingUsers}
      currentAdminId={session.user.id}
    />
  );
}
