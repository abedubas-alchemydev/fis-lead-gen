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
  updated_at: Date;
  feature_permissions: string[] | null;
  last_activity_at: Date | null;
};

type RemovedUserRow = {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: Date;
  feature_permissions: string[] | null;
  removed_at: Date | null;
  removed_action: string | null;
  removed_by_name: string | null;
  removed_by_email: string | null;
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

  const [pendingResult, activeResult, removedResult] = await Promise.all([
    db.query<PendingUserRow>(
      'SELECT id, email, name, created_at, email_verified FROM "user" WHERE status = $1 ORDER BY created_at ASC LIMIT 50',
      ["pending"]
    ),
    db.query<ActiveUserRow>(
      `SELECT u.id, u.email, u.name, u.role, u.created_at, u.updated_at, u.feature_permissions,
              MAX(s.last_activity_at) AS last_activity_at
       FROM "user" u
       LEFT JOIN "session" s ON s.user_id = u.id
       WHERE u.status = $1
       GROUP BY u.id
       ORDER BY u.created_at DESC
       LIMIT 200`,
      ["active"]
    ),
    // Removed accounts = status='rejected', bucketed by the most-recent
    // removal event in audit_log. 'user_deactivated' → was active and
    // an admin removed them; 'user_rejected' (or no audit row at all)
    // → never approved. audit_log.details is TEXT; cast to jsonb on
    // the fly to read target_user_id. audit_log.user_id is the actor.
    db.query<RemovedUserRow>(
      `SELECT u.id, u.email, u.name, u.role, u.created_at, u.feature_permissions,
              latest.timestamp AS removed_at,
              latest.action    AS removed_action,
              actor.name       AS removed_by_name,
              actor.email      AS removed_by_email
       FROM "user" u
       LEFT JOIN LATERAL (
         SELECT al.timestamp, al.user_id AS actor_id, al.action
         FROM audit_log al
         WHERE al.action IN ('user_deactivated', 'user_rejected')
           AND (al.details::jsonb)->>'target_user_id' = u.id
         ORDER BY al.timestamp DESC
         LIMIT 1
       ) latest ON TRUE
       LEFT JOIN "user" actor ON actor.id = latest.actor_id
       WHERE u.status = $1
       ORDER BY latest.timestamp DESC NULLS LAST, u.created_at DESC
       LIMIT 200`,
      ["rejected"]
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
    updatedAt: r.updated_at.toISOString(),
    featurePermissions: Array.isArray(r.feature_permissions) ? r.feature_permissions : [],
    lastActivityAt: r.last_activity_at ? r.last_activity_at.toISOString() : null,
  }));

  const removedUsers = removedResult.rows.map((r) => {
    const removedAction: "user_deactivated" | "user_rejected" | null =
      r.removed_action === "user_deactivated" || r.removed_action === "user_rejected"
        ? r.removed_action
        : null;
    return {
      id: r.id,
      email: r.email,
      name: r.name,
      role: r.role,
      createdAt: r.created_at.toISOString(),
      featurePermissions: Array.isArray(r.feature_permissions) ? r.feature_permissions : [],
      removedAt: r.removed_at ? r.removed_at.toISOString() : null,
      removedAction,
      removedByName: r.removed_by_name,
      removedByEmail: r.removed_by_email,
    };
  });

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <UsersAdminClient
        pendingUsers={pendingUsers}
        activeUsers={activeUsers}
        removedUsers={removedUsers}
        currentAdminId={session.user.id}
      />
    </div>
  );
}
