"use client";

import type { Route } from "next";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { AdvisorShareProfile } from "@/components/share/profiles/advisor-share-profile";
import { BankShareProfile } from "@/components/share/profiles/bank-share-profile";
import { BdShareProfile } from "@/components/share/profiles/bd-share-profile";
import type {
  AdvisorShareProfileProps,
  BankShareProfileProps,
  BdShareProfileProps,
} from "@/components/share/profiles/types";
import { Button } from "@/components/ui/button";
import type {
  PublicShareItemRow,
  PublicShareProfileResponse,
} from "@/types/public-share";

// Read-only profile frame for one shared item: back-to-list link, loading
// skeleton, error surface, and the kind dispatch into the per-kind profile
// renderers. The renderers (components/share/profiles/*) are a parallel
// slice built to this contract: each takes `{ data }` where data is the
// kind's payload object from the profile response envelope.
//
// All fetching lives in ShareClient — this component is purely
// presentational over (profile, loading, error).

export interface ShareProfileViewProps {
  token: string;
  // The list row for the active item — anchors the skeleton/error states
  // with the firm's name while (or when) the full profile isn't available.
  row: PublicShareItemRow;
  profile: PublicShareProfileResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

function renderProfile(profile: PublicShareProfileResponse) {
  if (profile.kind === "broker_dealer" && profile.broker_dealer != null) {
    return (
      <BdShareProfile
        data={profile.broker_dealer as BdShareProfileProps["data"]}
      />
    );
  }
  if (profile.kind === "bank" && profile.bank != null) {
    return <BankShareProfile data={profile.bank as BankShareProfileProps["data"]} />;
  }
  if (profile.kind === "advisor" && profile.advisor != null) {
    return (
      <AdvisorShareProfile
        data={profile.advisor as AdvisorShareProfileProps["data"]}
      />
    );
  }
  // Defensive: the envelope's kind key wasn't populated — surface it rather
  // than rendering a blank page.
  return (
    <div className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 text-sm text-[var(--text-dim,#475569)]">
      This profile can&apos;t be displayed.
    </div>
  );
}

export function ShareProfileView({
  token,
  row,
  profile,
  loading,
  error,
  onRetry,
}: ShareProfileViewProps) {
  const router = useRouter();

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6">
      <button
        type="button"
        onClick={() => router.push(`/share/${token}` as Route)}
        className="inline-flex items-center gap-1.5 rounded-lg text-sm font-medium text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/30"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden />
        Back to list
      </button>

      <div className="mt-5">
        {error ? (
          <div
            role="alert"
            className="rounded-2xl border border-red-200 bg-red-50 p-6"
          >
            <h1 className="text-base font-semibold text-red-800">
              Couldn&apos;t load {row.name}
            </h1>
            <p className="mt-1 text-sm text-red-700">{error}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={onRetry}
            >
              Try again
            </Button>
          </div>
        ) : loading || !profile ? (
          <div aria-busy="true" aria-live="polite">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
              Loading profile
            </p>
            <h1 className="mt-1 text-xl font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              {row.name}
            </h1>
            <div className="mt-5 space-y-4">
              <div className="h-24 animate-pulse rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]" />
              <div className="grid gap-4 sm:grid-cols-2">
                {[0, 1, 2, 3].map((index) => (
                  <div
                    key={index}
                    className="h-40 animate-pulse rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]"
                  />
                ))}
              </div>
            </div>
          </div>
        ) : (
          renderProfile(profile)
        )}
      </div>
    </div>
  );
}
