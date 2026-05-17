import { notFound } from "next/navigation";
import { ShieldAlert } from "lucide-react";

import { UserDetailClient } from "@/components/settings/user-detail-client";
import { db } from "@/lib/auth";
import { getRequiredSession } from "@/lib/auth-server";

type UserRow = {
  id: string;
  email: string;
  name: string;
  role: string;
  status: string;
  created_at: Date;
  email_verified: boolean;
  feature_permissions: string[] | null;
};

export const dynamic = "force-dynamic";

export default async function UserDetailPage({ params }: { params: { id: string } }) {
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
                User management is restricted
              </h1>
              <p className="max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                Only administrators can view or edit user accounts.
              </p>
            </div>
          </div>
        </section>
      </div>
    );
  }

  const result = await db.query<UserRow>(
    'SELECT id, email, name, role, status, created_at, email_verified, feature_permissions FROM "user" WHERE id = $1',
    [params.id]
  );
  if (result.rowCount === 0) {
    notFound();
  }
  const row = result.rows[0];

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <UserDetailClient
        user={{
          id: row.id,
          email: row.email,
          name: row.name,
          role: row.role,
          status: row.status,
          createdAt: row.created_at.toISOString(),
          emailVerified: row.email_verified,
          featurePermissions: Array.isArray(row.feature_permissions) ? row.feature_permissions : [],
        }}
        currentAdminId={session.user.id}
      />
    </div>
  );
}
