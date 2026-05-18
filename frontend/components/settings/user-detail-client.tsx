"use client";

import Link from "next/link";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Bookmark,
  CheckCircle2,
  Loader2,
  Save,
  ShieldCheck,
} from "lucide-react";

import {
  ADMIN_ONLY_FEATURE_KEYS,
  ALL_FEATURE_KEYS,
  ENABLED_FEATURE_KEYS,
  FEATURE_LABELS,
  type FeatureKey,
} from "@/lib/feature-permissions";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

type UserShape = {
  id: string;
  email: string;
  name: string;
  role: string;
  status: string;
  createdAt: string;
  emailVerified: boolean;
  featurePermissions: string[];
};

export function UserDetailClient({
  user,
  currentAdminId,
}: {
  user: UserShape;
  currentAdminId: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [selected, setSelected] = useState<Set<FeatureKey>>(
    () => new Set(user.featurePermissions.filter((k): k is FeatureKey => (ALL_FEATURE_KEYS as readonly string[]).includes(k)))
  );
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const isAdmin = user.role === "admin";
  const isSelf = user.id === currentAdminId;

  const initialSet = useMemo(
    () => new Set(user.featurePermissions),
    [user.featurePermissions]
  );
  const dirty = useMemo(() => {
    if (selected.size !== initialSet.size) return true;
    for (const k of selected) if (!initialSet.has(k)) return true;
    return false;
  }, [selected, initialSet]);

  function toggle(key: FeatureKey) {
    setSaved(false);
    setError(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function save() {
    setError(null);
    startTransition(async () => {
      try {
        const res = await fetch(`/api/admin/users/${user.id}/permissions`, {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            feature_permissions: Array.from(selected),
          }),
        });
        if (!res.ok) {
          let msg = `Request failed (${res.status})`;
          try {
            const body = await res.json();
            if (body?.error) msg = body.error;
          } catch {
            // non-JSON body — keep default message
          }
          throw new Error(msg);
        }
        setSaved(true);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Save failed.");
      }
    });
  }

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Workspace <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link href="/settings/users" className="hover:underline">Users</Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> {user.name || user.email}
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            {user.name || "(no name)"}
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            {user.email} · {user.role} · {user.status}
          </p>
        </div>
        <Link
          href="/settings/users"
          className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          Back to users
        </Link>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {saved && !dirty ? (
        <div className="flex items-start gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/12 px-4 py-3 text-sm text-[var(--pill-green-text,#047857)]">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>Feature access saved.</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Feature access</p>
            <h2 className={CARD_TITLE}>
              {isAdmin ? "Admins have full access to every feature" : "Check the lists this user should see"}
            </h2>
          </div>
          {isAdmin ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/12 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-[var(--pill-green-text,#047857)]">
              <ShieldCheck className="h-3 w-3" strokeWidth={2.5} aria-hidden />
              Admin
            </span>
          ) : null}
        </div>

        <ul className="mt-4 divide-y divide-[var(--border,rgba(30,64,175,0.1))] rounded-xl border border-[var(--border,rgba(30,64,175,0.1))]">
          {ALL_FEATURE_KEYS.map((key) => {
            const enabled = ENABLED_FEATURE_KEYS.has(key);
            const adminOnly = ADMIN_ONLY_FEATURE_KEYS.has(key);
            const disabled = isAdmin || !enabled || isPending || adminOnly;
            const checked = isAdmin || selected.has(key);
            const caption = adminOnly ? "Admin only" : !enabled ? "Coming soon" : null;
            return (
              <li key={key} className="flex items-center gap-3 px-4 py-3">
                <input
                  id={`perm-${key}`}
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggle(key)}
                  className="h-4 w-4 rounded border-[var(--border-2,rgba(30,64,175,0.16))] text-emerald-600 focus:ring-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                />
                <label
                  htmlFor={`perm-${key}`}
                  className={`flex-1 text-sm font-medium ${disabled && !isAdmin ? "text-[var(--text-muted,#94a3b8)]" : "text-[var(--text,#0f172a)]"}`}
                >
                  {FEATURE_LABELS[key]}
                </label>
                {caption ? (
                  <span className="text-[11px] uppercase tracking-[0.04em] text-[var(--text-muted,#94a3b8)]">
                    {caption}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>

        <div className="mt-4 flex items-center justify-between gap-3">
          <p className="text-xs text-[var(--text-muted,#94a3b8)]">
            {isAdmin
              ? "Demote this account to a viewer to apply per-feature gating."
              : isSelf
                ? "Editing your own access — changes apply within ~5 minutes."
                : "Changes apply within ~5 minutes (session cookie cache)."}
          </p>
          <button
            type="button"
            onClick={save}
            disabled={isAdmin || !dirty || isPending}
            className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-500 px-3 py-2 text-xs font-semibold text-white shadow-[0_6px_16px_rgba(16,185,129,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none"
          >
            {isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden />
            )}
            Save
          </button>
        </div>
      </div>

      <Link
        href={`/settings/users/${user.id}/saved-firms` as Route}
        className={`${CARD} flex items-center gap-4 transition hover:bg-[var(--surface-2,#f1f6fd)]/50`}
      >
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#1e40af)]">
          <Bookmark className="h-5 w-5" aria-hidden />
        </div>
        <div className="flex-1">
          <p className={EYEBROW}>Saved firms</p>
          <h2 className={CARD_TITLE}>
            View the firms this user has saved
          </h2>
          <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
            See every broker-dealer and investment advisor across their lists.
          </p>
        </div>
        <ArrowRight className="h-4 w-4 text-[var(--text-muted,#94a3b8)]" aria-hidden />
      </Link>

      <Link
        href={`/settings/users/${user.id}/activities` as Route}
        className={`${CARD} flex items-center gap-4 transition hover:bg-[var(--surface-2,#f1f6fd)]/50`}
      >
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#1e40af)]">
          <Activity className="h-5 w-5" aria-hidden />
        </div>
        <div className="flex-1">
          <p className={EYEBROW}>Activity</p>
          <h2 className={CARD_TITLE}>
            View this user&apos;s activity feed
          </h2>
          <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
            Login history, firm views, saves, and outreach sends — one timestamp-ordered stream.
          </p>
        </div>
        <ArrowRight className="h-4 w-4 text-[var(--text-muted,#94a3b8)]" aria-hidden />
      </Link>
    </section>
  );
}
