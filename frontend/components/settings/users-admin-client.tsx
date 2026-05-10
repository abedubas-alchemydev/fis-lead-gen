"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Inbox,
  Loader2,
  Mail,
  ShieldAlert,
  XCircle,
} from "lucide-react";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

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

  const headlineCount = pendingUsers.length;

  return (
    <section className="space-y-6">
      {/* Page header — mirrors /dashboard + /settings topbar typography. */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span> Users
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            User approvals
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Approve or reject new self-signups. Approved users can sign in on their next attempt.
            Rejected users are signed out and cannot sign in again.
          </p>
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Pending signups</p>
            <h2 className={CARD_TITLE}>
              {headlineCount === 0
                ? "Nothing waiting on review"
                : `${headlineCount} ${headlineCount === 1 ? "account" : "accounts"} awaiting approval`}
            </h2>
          </div>
          {headlineCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/25 bg-amber-500/12 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-amber-700">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden />
              Pending
            </span>
          ) : null}
        </div>

        {headlineCount === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
            <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
              <Inbox className="h-6 w-6" strokeWidth={1.75} aria-hidden />
            </div>
            <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
              No signups pending approval
            </p>
            <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
              New accounts awaiting review will appear here as they come in.
            </p>
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
            <table className="w-full text-left text-sm">
              <thead className="bg-[var(--surface-2,#f1f6fd)] text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="px-5 py-3">Name</th>
                  <th className="px-5 py-3">Email</th>
                  <th className="px-5 py-3">Signed up</th>
                  <th className="px-5 py-3">Verified</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {pendingUsers.map((u) => {
                  const isSelf = u.id === currentAdminId;
                  const isActing = actingId === u.id && isPending;
                  return (
                    <tr key={u.id} className="hover:bg-[var(--surface-2,#f1f6fd)]/50">
                      <td className="px-5 py-4 font-semibold text-[var(--text,#0f172a)]">
                        {u.name || "—"}
                      </td>
                      <td className="px-5 py-4 text-[var(--text-dim,#475569)]">
                        <span className="inline-flex items-center gap-1.5">
                          <Mail className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" aria-hidden />
                          {u.email}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-[var(--text-muted,#94a3b8)]">
                        <span className="inline-flex items-center gap-1.5">
                          <Clock className="h-3.5 w-3.5" aria-hidden />
                          {new Date(u.createdAt).toLocaleString()}
                        </span>
                      </td>
                      <td className="px-5 py-4">
                        {u.emailVerified ? (
                          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/25 bg-emerald-500/12 px-2.5 py-0.5 text-[11px] font-semibold text-[var(--pill-green-text,#047857)]">
                            <CheckCircle2 className="h-3 w-3" strokeWidth={2.5} aria-hidden />
                            Verified
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/12 px-2.5 py-0.5 text-[11px] font-semibold text-amber-700">
                            Not yet
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right">
                        {isSelf ? (
                          <span className="inline-flex items-center gap-1 text-xs text-[var(--text-muted,#94a3b8)]">
                            <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
                            Cannot modify own row
                          </span>
                        ) : (
                          <div className="inline-flex gap-2">
                            <button
                              type="button"
                              onClick={() => act(u.id, "approve")}
                              disabled={isActing}
                              className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-500 px-3 py-2 text-xs font-semibold text-white shadow-[0_6px_16px_rgba(16,185,129,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none"
                            >
                              {isActing ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                              ) : (
                                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
                              )}
                              Approve
                            </button>
                            <button
                              type="button"
                              onClick={() => act(u.id, "reject")}
                              disabled={isActing}
                              className="inline-flex items-center gap-1.5 rounded-xl border border-red-500/25 bg-transparent px-3 py-2 text-xs font-semibold text-[var(--pill-red-text,#b91c1c)] transition hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {isActing ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                              ) : (
                                <XCircle className="h-3.5 w-3.5" aria-hidden />
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
      </div>
    </section>
  );
}
