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

type ActiveUserRow = {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: Date;
  feature_permissions: string[] | null;
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

  const [pendingResult, activeResult] = await Promise.all([
    db.query<PendingUserRow>(
      'SELECT id, email, name, created_at, email_verified FROM "user" WHERE status = $1 ORDER BY created_at ASC LIMIT 50',
      ["pending"]
    ),
    db.query<ActiveUserRow>(
      'SELECT id, email, name, role, created_at, feature_permissions FROM "user" WHERE status = $1 ORDER BY created_at DESC LIMIT 200',
      ["active"]
    ),
  ]);

  const pendingUsers = pendingResult.rows.map((r) => ({
    id: r.id,
    email: r.email,
    name: r.name,
    createdAt: r.created_at.toISOString(),
    emailVerified: r.email_verified,
  }));

  const activeUsers = activeResult.rows.map((r) => ({
    id: r.id,
    email: r.email,
    name: r.name,
    role: r.role,
    createdAt: r.created_at.toISOString(),
    featurePermissions: Array.isArray(r.feature_permissions) ? r.feature_permissions : [],
  }));

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <UsersAdminClient
        pendingUsers={pendingUsers}
        activeUsers={activeUsers}
        currentAdminId={session.user.id}
      />
    </div>
  );
}
