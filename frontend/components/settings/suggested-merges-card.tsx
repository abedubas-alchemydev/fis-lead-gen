"use client";

import { AlertCircle, Check, Inbox, Loader2, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState, useTransition } from "react";

import { apiRequest } from "@/lib/api";
import type {
  ClearingPartnerClusteringRunResponse,
  ClearingPartnerMergeSuggestionAccept,
  ClearingPartnerMergeSuggestionItem,
  ClearingPartnerMergeSuggestionList,
} from "@/lib/types";

// Design tokens duplicated from pipeline-admin-client.tsx so both files
// stay self-contained — extracting to a shared module would be premature
// for two card surfaces.
const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

const INPUT_CLASS =
  "w-full rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] transition focus:border-[var(--accent,#6366f1)] focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30";

// Card surfaces the clustering pass's pending suggestions and lets admin
// accept (creates a CompetitorProvider with the variants as aliases) or
// reject. See plans/be-clearing-partner-suggested-merges-2026-05-08.md
// for the BE contract.
export function SuggestedMergesCard({ onAccepted }: { onAccepted?: () => void }) {
  const [suggestions, setSuggestions] = useState<ClearingPartnerMergeSuggestionItem[]>(
    [],
  );
  const [pendingCount, setPendingCount] = useState(0);
  const [unmatchedCount, setUnmatchedCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPending, startTransition] = useTransition();

  const loadSuggestions = useCallback(async () => {
    try {
      const response = await apiRequest<ClearingPartnerMergeSuggestionList>(
        "/api/v1/settings/clearing-partner-suggestions?status=pending&page_size=100",
      );
      setSuggestions(response.items);
      setPendingCount(response.pending_count);
      setUnmatchedCount(response.unmatched_count);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load merge suggestions.",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSuggestions();
  }, [loadSuggestions]);

  function runScan() {
    startTransition(async () => {
      setError(null);
      setInfo(null);
      try {
        const response = await apiRequest<ClearingPartnerClusteringRunResponse>(
          "/api/v1/settings/clearing-partner-suggestions/run",
          { method: "POST" },
        );
        const remaining = response.unmatched_count;
        const remainingSuffix =
          remaining > 0
            ? ` ${remaining.toLocaleString()} raw string${remaining === 1 ? "" : "s"} still unmatched.`
            : " Everything is clustered or already canonical.";
        setInfo(
          (response.new_pending_count === 0
            ? "No new clusters found."
            : `Found ${response.new_pending_count} new cluster${response.new_pending_count === 1 ? "" : "s"}.`) +
            remainingSuffix,
        );
        await loadSuggestions();
      } catch (actionError) {
        setError(
          actionError instanceof Error
            ? actionError.message
            : "Unable to run clustering scan.",
        );
      }
    });
  }

  function acceptSuggestion(
    suggestionId: number,
    payload: ClearingPartnerMergeSuggestionAccept,
  ) {
    startTransition(async () => {
      setError(null);
      setInfo(null);
      try {
        await apiRequest(
          `/api/v1/settings/clearing-partner-suggestions/${suggestionId}/accept`,
          {
            method: "POST",
            body: JSON.stringify(payload),
          },
        );
        setInfo(`Accepted '${payload.canonical_name}'.`);
        await loadSuggestions();
        onAccepted?.();
      } catch (actionError) {
        setError(
          actionError instanceof Error
            ? actionError.message
            : "Unable to accept suggestion.",
        );
      }
    });
  }

  function rejectSuggestion(suggestionId: number) {
    startTransition(async () => {
      setError(null);
      setInfo(null);
      try {
        await apiRequest(
          `/api/v1/settings/clearing-partner-suggestions/${suggestionId}/reject`,
          { method: "POST" },
        );
        await loadSuggestions();
      } catch (actionError) {
        setError(
          actionError instanceof Error
            ? actionError.message
            : "Unable to reject suggestion.",
        );
      }
    });
  }

  return (
    <div className={CARD}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className={EYEBROW}>Suggested Merges</p>
          <h2 className={CARD_TITLE}>
            Clearing-partner duplicates
            {pendingCount > 0 ? (
              <span className="ml-2 inline-flex items-center rounded-full bg-[rgba(99,102,241,0.12)] px-2 py-0.5 text-[11px] font-semibold text-[#4338ca]">
                {pendingCount} pending
              </span>
            ) : null}
            {unmatchedCount > 0 ? (
              <span className="ml-2 inline-flex items-center rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[11px] font-semibold text-[var(--text-dim,#475569)]">
                {unmatchedCount.toLocaleString()} unmatched
              </span>
            ) : null}
          </h2>
          <p className="mt-1.5 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Cluster pass groups raw variants of the same firm
            (&ldquo;RBC Capital Markets, LLC&rdquo;, &ldquo;RBC Capital Markets
            LLC&rdquo;, etc.) so they collapse into one Clearing Arrangement
            entry. Review each cluster and accept to create a competitor
            provider with the variants as aliases.
          </p>
        </div>
        <button
          type="button"
          disabled={isPending}
          onClick={runScan}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-4 py-2 text-sm font-semibold text-[var(--text,#0f172a)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Plus className="h-4 w-4" aria-hidden />
          )}
          Run scan
        </button>
      </div>

      {error ? (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}
      {info ? (
        <p className="mt-4 text-[12px] text-[var(--text-dim,#475569)]">{info}</p>
      ) : null}

      <div className="mt-5 space-y-3">
        {isLoading ? (
          <p className="text-sm text-[var(--text-muted,#94a3b8)]">
            Loading suggestions…
          </p>
        ) : suggestions.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-8 text-center">
            <div className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-dim,#475569)]">
              <Inbox className="h-5 w-5" strokeWidth={1.75} aria-hidden />
            </div>
            <p className="mt-3 text-sm font-semibold text-[var(--text,#0f172a)]">
              No pending merge suggestions
            </p>
            <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
              Run a scan to look for duplicate clearing-partner variants.
            </p>
          </div>
        ) : (
          suggestions.map((suggestion) => (
            <SuggestionRow
              key={suggestion.id}
              suggestion={suggestion}
              isPending={isPending}
              onAccept={acceptSuggestion}
              onReject={rejectSuggestion}
            />
          ))
        )}
      </div>
    </div>
  );
}

const INPUT_CLASS_COMPACT =
  `${INPUT_CLASS} px-2.5 py-1.5 text-[13px]`;

function SuggestionRow({
  suggestion,
  isPending,
  onAccept,
  onReject,
}: {
  suggestion: ClearingPartnerMergeSuggestionItem;
  isPending: boolean;
  onAccept: (id: number, payload: ClearingPartnerMergeSuggestionAccept) => void;
  onReject: (id: number) => void;
}) {
  const [canonicalName, setCanonicalName] = useState(suggestion.suggested_name);
  const [displayName, setDisplayName] = useState("");
  const [priority, setPriority] = useState(90);
  const [includedVariants, setIncludedVariants] = useState<Set<string>>(
    () => new Set(suggestion.variants),
  );

  const toggleVariant = (variant: string) => {
    setIncludedVariants((current) => {
      const next = new Set(current);
      if (next.has(variant)) next.delete(variant);
      else next.add(variant);
      return next;
    });
  };

  const canAccept =
    canonicalName.trim().length > 0 && includedVariants.size > 0 && !isPending;

  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Score
        </span>
        <span className="rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 font-mono text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          {suggestion.min_score} / 100
        </span>
        <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          {suggestion.variants.length} variants
        </span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="block">
          <span className="block text-xs font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Canonical name
          </span>
          <input
            value={canonicalName}
            onChange={(event) => setCanonicalName(event.target.value)}
            maxLength={255}
            className={`${INPUT_CLASS_COMPACT} mt-1.5`}
          />
        </label>
        <label className="block">
          <span className="block text-xs font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Display name <span className="normal-case text-[var(--text-muted,#94a3b8)]">(optional)</span>
          </span>
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={50}
            placeholder="Short label for the dropdown"
            className={`${INPUT_CLASS_COMPACT} mt-1.5`}
          />
        </label>
      </div>

      <div className="mt-3">
        <p className="text-xs font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
          Variants
        </p>
        <ul className="mt-1.5 space-y-1">
          {suggestion.variants.map((variant) => {
            const checked = includedVariants.has(variant);
            return (
              <li key={variant}>
                <label
                  className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)] ${
                    checked
                      ? "text-[var(--text,#0f172a)]"
                      : "text-[var(--text-muted,#94a3b8)] line-through"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleVariant(variant)}
                    className="h-4 w-4 cursor-pointer accent-[var(--accent,#6366f1)]"
                  />
                  <span className="min-w-0 flex-1 truncate">{variant}</span>
                </label>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Priority
          <input
            type="number"
            value={priority}
            onChange={(event) => setPriority(Number(event.target.value))}
            min={1}
            max={999}
            className="h-7 w-16 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2 font-mono text-[12px] text-[var(--text,#0f172a)] focus:border-[var(--accent,#6366f1)] focus:outline-none"
          />
        </label>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            disabled={isPending}
            onClick={() => onReject(suggestion.id)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-transparent px-3 py-1.5 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
            Reject
          </button>
          <button
            type="button"
            disabled={!canAccept}
            onClick={() =>
              onAccept(suggestion.id, {
                canonical_name: canonicalName.trim(),
                display_name: displayName.trim() ? displayName.trim() : null,
                variants: Array.from(includedVariants),
                priority,
              })
            }
            className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent,#6366f1)] px-3 py-1.5 text-[12px] font-semibold text-white shadow-[0_4px_12px_rgba(99,102,241,0.25)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            <Check className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
            Accept
          </button>
        </div>
      </div>
    </div>
  );
}
