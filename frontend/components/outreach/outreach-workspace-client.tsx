"use client";

import { useCallback } from "react";
import type { ReadonlyURLSearchParams } from "next/navigation";

import { CreateOutreachTab } from "@/components/outreach/create-outreach-tab";
import { SentHistoryTab } from "@/components/outreach/sent-history-tab";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";

// /outreach/sent is a two-tab workspace:
//   - "Sent history" (default) -- the audit list of every email the
//     user (or, for admins on scope=all, every user) has transmitted.
//   - "Create outreach" -- compose-and-send for an arbitrary recipient,
//     either picked from existing contacts or typed as a free-form
//     email. See create-outreach-tab.tsx.
//
// Tab state is URL-synced so share-links + Back/Forward round-trip.
// Default = "history" because the route is /outreach/sent and we want
// existing bookmarks / sidebar nav to keep landing on the audit list.

type OutreachTab = "create" | "history";

type WorkspaceState = { tab: OutreachTab };

// `parse` + `build` must be stable module-level references for
// `useUrlSyncedState` to avoid effect re-runs every render.
function parseUrl(sp: ReadonlyURLSearchParams): WorkspaceState {
  return { tab: sp.get("tab") === "create" ? "create" : "history" };
}

function buildUrl(state: WorkspaceState): string {
  return state.tab === "create"
    ? "/outreach/sent?tab=create"
    : "/outreach/sent";
}

const TABS: ReadonlyArray<{ value: OutreachTab; label: string }> = [
  { value: "create", label: "Create outreach" },
  { value: "history", label: "Sent history" },
];

export function OutreachWorkspaceClient({
  isAdmin = false,
  currentUserId,
}: {
  isAdmin?: boolean;
  currentUserId: string;
}) {
  const { state, updateState } = useUrlSyncedState(parseUrl, buildUrl);
  const tab = state.tab;
  const setTab = useCallback(
    (next: OutreachTab) => updateState({ tab: next }),
    [updateState],
  );

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Vault <span className="text-[var(--text-dim,#475569)]">/</span> Outreach
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Outreach
          </h1>
          <p className="mt-2 max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            {tab === "create"
              ? "Compose a new outreach email to a contact in your pipeline, or send a one-off to any email address. Drafts are powered by your linked Gmail / Microsoft / Yahoo account."
              : isAdmin
                ? "Every outreach email sent across all users, including failed attempts and the reason they didn't go through. Click a row to read the full message."
                : "Every outreach email you've sent, plus any failed attempts and the reason they didn't go through. Click a row to read the full message."}
          </p>
        </div>
      </div>

      {/* ── Tabs ──────────────────────────────────────────────────── */}
      <div className="inline-flex rounded-[12px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-1">
        {TABS.map((entry) => {
          const active = entry.value === tab;
          return (
            <button
              key={entry.value}
              type="button"
              aria-pressed={active}
              onClick={() => setTab(entry.value)}
              className={`inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-[13px] transition ${
                active
                  ? "bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)]"
                  : "font-medium text-[var(--text-dim,#475569)] hover:bg-[var(--surface,#ffffff)] hover:text-[var(--text,#0f172a)]"
              }`}
            >
              {entry.label}
            </button>
          );
        })}
      </div>

      {tab === "create" ? (
        <CreateOutreachTab />
      ) : (
        <SentHistoryTab isAdmin={isAdmin} currentUserId={currentUserId} />
      )}
    </section>
  );
}
