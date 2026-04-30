"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Clock, Loader2, Mail, ShieldAlert, XCircle } from "lucide-react";

type PendingUser = {
  id: string;
  email: string;
  name: string;
  createdAt: string;
  emailVerified: boolean;
};

export function UsersAdminClient({
  pendingUsers,
  currentAdminId,
}: {
  pendingUsers: PendingUser[];
  currentAdminId: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [actingId, setActingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(userId: string, action: "approve" | "reject") {
    setError(null);
    setActingId(userId);
    startTransition(async () => {
      try {
        const res = await fetch(`/api/admin/users/${userId}/${action}`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) {
          let msg = `Request failed (${res.status})`;
          try {
            const body = await res.json();
            if (body?.error) msg = body.error;
          } catch {
            // non-JSON response — keep default msg
          }
          throw new Error(msg);
        }
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Action failed.");
      } finally {
        setActingId(null);
      }
    });
  }

  return (
    <section className="space-y-6">
      <header className="space-y-2">
        <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">User approvals</p>
        <h1 className="text-2xl font-semibold text-navy">Pending signups</h1>
        <p className="max-w-2xl text-sm leading-6 text-slate-600">
          Approve or reject new self-signups. Approved users can sign in on their next attempt.
          Rejected users are signed out and cannot sign in again.
        </p>
      </header>

      {error ? (
        <div className="rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      ) : null}

      {pendingUsers.length === 0 ? (
        <div className="rounded-[24px] border border-emerald-100 bg-emerald-50/50 p-6 shadow-shell">
          <div className="flex items-start gap-3">
            <div className="rounded-xl bg-white p-2.5 text-success">
              <CheckCircle2 className="h-5 w-5" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-emerald-800">No signups pending approval</p>
              <p className="text-sm text-emerald-700">
                New accounts awaiting review will appear here as they come in.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[24px] border border-slate-200 bg-white shadow-shell">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-5 py-3">Name</th>
                <th className="px-5 py-3">Email</th>
                <th className="px-5 py-3">Signed up</th>
                <th className="px-5 py-3">Verified</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {pendingUsers.map((u) => {
                const isSelf = u.id === currentAdminId;
                const isActing = actingId === u.id && isPending;
                return (
                  <tr key={u.id}>
                    <td className="px-5 py-4 font-medium text-navy">{u.name || "—"}</td>
                    <td className="px-5 py-4 text-slate-700">
                      <span className="inline-flex items-center gap-1.5">
                        <Mail className="h-3.5 w-3.5 text-slate-400" />
                        {u.email}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-slate-500">
                      <span className="inline-flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5 text-slate-400" />
                        {new Date(u.createdAt).toLocaleString()}
                      </span>
                    </td>
                    <td className="px-5 py-4">
                      {u.emailVerified ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-success">
                          <CheckCircle2 className="h-3 w-3" /> Yes
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-warning">
                          Not yet
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-right">
                      {isSelf ? (
                        <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                          <ShieldAlert className="h-3.5 w-3.5" /> Cannot modify own row
                        </span>
                      ) : (
                        <div className="inline-flex gap-2">
                          <button
                            onClick={() => act(u.id, "approve")}
                            disabled={isActing}
                            className="inline-flex items-center gap-1.5 rounded-xl bg-success px-3 py-2 text-xs font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
                          >
                            {isActing ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <CheckCircle2 className="h-3.5 w-3.5" />
                            )}
                            Approve
                          </button>
                          <button
                            onClick={() => act(u.id, "reject")}
                            disabled={isActing}
                            className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-danger transition hover:bg-red-50 disabled:opacity-50"
                          >
                            {isActing ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <XCircle className="h-3.5 w-3.5" />
                            )}
                            Reject
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
