"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AlertCircle,
  CheckCircle2,
  Inbox,
  Loader2,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import {
  approveClearingMembership,
  getClearingMembershipReviewQueue,
  rejectClearingMembership,
} from "@/lib/api";
import { agencyLabel } from "@/components/master-list/detail/clearing-membership-helpers";
import { Pill } from "@/components/ui/pill";
import type { ClearingMembershipReviewRow } from "@/lib/types";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

type Group = {
  key: string;
  agency: string;
  memberName: string;
  sourceFile: string;
  candidates: ClearingMembershipReviewRow[];
};

function groupRows(rows: ClearingMembershipReviewRow[]): Group[] {
  const map = new Map<string, Group>();
  for (const row of rows) {
    const key = `${row.agency}|${row.member_name_raw}`;
    let g = map.get(key);
    if (g === undefined) {
      g = {
        key,
        agency: row.agency,
        memberName: row.member_name_raw,
        sourceFile: row.source_file,
        candidates: [],
      };
      map.set(key, g);
    }
    g.candidates.push(row);
  }
  return Array.from(map.values()).sort((a, b) =>
    a.memberName.localeCompare(b.memberName) || a.agency.localeCompare(b.agency),
  );
}

export function ClearingMembershipsAdminClient() {
  const [rows, setRows] = useState<ClearingMembershipReviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [actingId, setActingId] = useState<number | null>(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getClearingMembershipReviewQueue({ limit: 500 });
      setRows(resp.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load review queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const groups = useMemo(() => (rows === null ? [] : groupRows(rows)), [rows]);

  async function act(id: number, decision: "approve" | "reject") {
    setActingId(id);
    setError(null);
    try {
      if (decision === "approve") {
        await approveClearingMembership(id);
      } else {
        await rejectClearingMembership(id);
      }
      await refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <p className={EYEBROW}>Reputable-Dispute Membership Review</p>
        <h1 className="text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          Adjudicate ambiguous directory matches
        </h1>
        <p className="max-w-3xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          The OCC/DTCC directory importer routes ambiguous name matches (one directory entry
          mapping to more than one of our firms) to <span className="font-mono text-[12px]">needs_review</span>{" "}
          so labels never auto-apply when we can&apos;t tell which firm was meant. Approve the
          right candidate — that flips it to <span className="font-mono text-[12px]">active</span> and
          stamps <span className="font-mono text-[12px]">match_method=&apos;manual&apos;</span> so a re-import preserves
          the decision. Sibling candidates auto-reject on approve.
        </p>
      </header>

      {error ? (
        <div className="flex items-start gap-3 rounded-xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.06)] p-4 text-[13px] text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      {loading && rows === null ? (
        <div className={CARD}>
          <div className="flex items-center gap-3 text-[13px] text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading review queue…
          </div>
        </div>
      ) : groups.length === 0 ? (
        <div className={CARD}>
          <div className="flex items-start gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface-2,#f1f6fd)] text-[var(--text-muted,#94a3b8)]">
              <Inbox className="h-5 w-5" aria-hidden />
            </div>
            <div className="space-y-1">
              <p className={EYEBROW}>Empty</p>
              <h2 className={CARD_TITLE}>Nothing to review</h2>
              <p className="text-[13px] text-[var(--text-dim,#475569)]">
                Every directory entry either matched exactly one firm (auto-applied) or matched
                none (recorded in the importer&apos;s unmatched report).
              </p>
            </div>
          </div>
        </div>
      ) : (
        groups.map((g) => (
          <article key={g.key} className={CARD}>
            <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Pill variant="member">{agencyLabel(g.agency)}</Pill>
                  <span className={EYEBROW}>Directory entry</span>
                </div>
                <h2 className={CARD_TITLE}>{g.memberName}</h2>
                <p className="font-mono text-[11px] text-[var(--text-muted,#94a3b8)]">
                  {g.sourceFile}
                </p>
              </div>
              <p className="text-[12px] text-[var(--text-dim,#475569)]">
                {g.candidates.length} candidate{g.candidates.length === 1 ? "" : "s"}
              </p>
            </header>

            <ul className="space-y-2">
              {g.candidates.map((c) => {
                const isActing = actingId === c.id;
                return (
                  <li
                    key={c.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-3"
                  >
                    <div className="min-w-0 space-y-0.5">
                      <p className="truncate text-[13px] font-medium text-[var(--text,#0f172a)]">
                        {c.firm_name}
                      </p>
                      <p className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                        {c.firm_side === "broker_dealer" ? "Broker-dealer" : "Investment advisor"}
                        {" · id "}
                        {c.firm_id}
                        {" · matched via "}
                        <span className="font-mono">{c.match_method}</span>
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void act(c.id, "approve")}
                        disabled={isActing}
                        className="inline-flex items-center gap-1.5 rounded-[8px] border border-[rgba(16,185,129,0.25)] bg-[rgba(16,185,129,0.12)] px-3 py-1.5 text-[12px] font-semibold text-[var(--pill-green-text,#047857)] transition hover:bg-[rgba(16,185,129,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
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
                        onClick={() => void act(c.id, "reject")}
                        disabled={isActing}
                        className="inline-flex items-center gap-1.5 rounded-[8px] border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-3 py-1.5 text-[12px] font-semibold text-[var(--pill-red-text,#b91c1c)] transition hover:bg-[rgba(239,68,68,0.14)] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <XCircle className="h-3.5 w-3.5" aria-hidden />
                        Reject
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </article>
        ))
      )}

      {!loading && groups.length > 0 ? (
        <p className="flex items-center gap-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
          <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
          Approving a candidate marks it as the canonical match for that directory entry; sibling
          candidates auto-reject. A future re-import won&apos;t overwrite manual decisions.
        </p>
      ) : null}
    </div>
  );
}
