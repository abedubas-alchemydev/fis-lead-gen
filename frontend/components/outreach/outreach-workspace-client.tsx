"use client";

import { useCallback, useState } from "react";
import type { ReadonlyURLSearchParams } from "next/navigation";

import { CreateOutreachTab } from "@/components/outreach/create-outreach-tab";
import { DraftsTab } from "@/components/outreach/drafts-tab";
import { SentHistoryTab } from "@/components/outreach/sent-history-tab";
import { useUrlSyncedState } from "@/lib/use-url-synced-state";
import type { SavedOutreachDraftDetail } from "@/lib/types";

// /outreach/sent is a three-tab workspace:
//   - "Create outreach" -- compose-and-send for an arbitrary recipient,
//     picked from existing contacts or typed as a free-form email. Can be
//     saved as a draft instead of sent.
//   - "Drafts" -- saved-but-unsent composer drafts (incl. Doxie-authored
//     ones). "Edit" loads a draft back into Create, pre-filled.
//   - "Sent history" (default) -- the audit list of every email sent.
//
// Tab state is URL-synced so share-links + Back/Forward round-trip.
// Default = "history" because the route is /outreach/sent and we want
// existing bookmarks / sidebar nav to keep landing on the audit list.

type OutreachTab = "create" | "drafts" | "history";

type WorkspaceState = { tab: OutreachTab };

// `parse` + `build` must be stable module-level references for
// `useUrlSyncedState` to avoid effect re-runs every render.
function parseUrl(sp: ReadonlyURLSearchParams): WorkspaceState {
  const tab = sp.get("tab");
  return { tab: tab === "create" || tab === "drafts" ? tab : "history" };
}

function buildUrl(state: WorkspaceState): string {
  if (state.tab === "create") return "/outreach/sent?tab=create";
  if (state.tab === "drafts") return "/outreach/sent?tab=drafts";
  return "/outreach/sent";
}

const TABS: ReadonlyArray<{ value: OutreachTab; label: string }> = [
  { value: "create", label: "Create outreach" },
  { value: "drafts", label: "Drafts" },
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
  // The draft currently being edited (Drafts tab "Edit" -> Create tab).
  // Lifted here so the Drafts tab can hand a draft to the composer across
  // the tab switch; cleared whenever the user opens Create fresh.
  const [editingDraft, setEditingDraft] =
    useState<SavedOutreachDraftDetail | null>(null);

  const goToTab = useCallback(
    (next: OutreachTab) => {
      // A manual click on "Create outreach" starts a fresh compose; the
      // Edit-draft flow uses editFromDraft (which keeps the draft) instead.
      if (next === "create") setEditingDraft(null);
      updateState({ tab: next });
    },
    [updateState],
  );

  const editFromDraft = useCallback(
    (draft: SavedOutreachDraftDetail) => {
      setEditingDraft(draft);
      updateState({ tab: "create" });
    },
    [updateState],
  );

  const description =
    tab === "create"
      ? "Compose a new outreach email to a contact in your pipeline, or send a one-off to any email address. Save it as a draft to finish later, or send it now via your linked Gmail / Microsoft / Yahoo account."
      : tab === "drafts"
        ? "Emails you've saved but haven't sent yet, including drafts Doxie generated for you. Open one to keep editing, then send it from the composer."
        : isAdmin
          ? "Every outreach email sent across all users, including failed attempts and the reason they didn't go through. Click a row to read the full message."
          : "Every outreach email you've sent, plus any failed attempts and the reason they didn't go through. Click a row to read the full message.";

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
            {description}
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
              onClick={() => goToTab(entry.value)}
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
        <CreateOutreachTab
          // Remount when the chosen draft changes so the composer re-hydrates
          // from initialDraft via its useState initializers.
          key={editingDraft ? `draft-${editingDraft.id}` : "new"}
          initialDraft={editingDraft}
        />
      ) : tab === "drafts" ? (
        <DraftsTab onEditDraft={editFromDraft} />
      ) : (
        <SentHistoryTab isAdmin={isAdmin} currentUserId={currentUserId} />
      )}
    </section>
  );
}
