"use client";

import { Loader2, Sparkles, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useToast } from "@/components/ui/use-toast";
import { apiRequest } from "@/lib/api";
import {
  cancelEnrichAll,
  enrichAll,
  type EnrichAllResponse,
} from "@/lib/email-extractor";

const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 100;

type PollEnrichmentStatus = "not_enriched" | "enriched" | "no_match" | "error";

interface PollScanShape {
  enrich_cancelled_at: string | null;
  discovered_emails: Array<{ enrichment_status: PollEnrichmentStatus }>;
}

export interface EnrichAllSummary {
  enrichedCount: number;
  failedCount: number;
  total: number;
  timedOut: boolean;
  cancelled?: boolean;
}

export interface EnrichAllButtonProps {
  scanId: number;
  unenrichedCount: number;
  onProgress?: () => void;
  onDone?: (summary: EnrichAllSummary) => void;
}

function countStatuses(scan: PollScanShape): { enriched: number; failed: number; total: number } {
  const total = scan.discovered_emails.length;
  let enriched = 0;
  let failed = 0;
  for (const row of scan.discovered_emails) {
    if (row.enrichment_status === "enriched") enriched += 1;
    else if (row.enrichment_status === "no_match" || row.enrichment_status === "error") failed += 1;
  }
  return { enriched, failed, total };
}

export function EnrichAllButton({
  scanId,
  unenrichedCount,
  onProgress,
  onDone,
}: EnrichAllButtonProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [optimisticQueued, setOptimisticQueued] = useState(0);
  const [statusText, setStatusText] = useState<string | null>(null);
  const toast = useToast();

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);
  const mountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  const handleClick = useCallback(async () => {
    if (isRunning || unenrichedCount <= 0) return;
    setIsRunning(true);
    setIsStopping(false);
    setStatusText(`Enriching ${unenrichedCount}…`);
    setOptimisticQueued(unenrichedCount);
    pollCountRef.current = 0;

    let queued: EnrichAllResponse;
    try {
      queued = await enrichAll(scanId);
    } catch {
      if (!mountedRef.current) return;
      setIsRunning(false);
      setOptimisticQueued(0);
      setStatusText(null);
      toast.error("Couldn't start enrichment — please try again.");
      return;
    }

    if (!mountedRef.current) return;

    if (queued.candidates_queued === 0) {
      setIsRunning(false);
      setOptimisticQueued(0);
      setStatusText("Nothing to enrich — all rows already processed.");
      onDone?.({
        enrichedCount: queued.candidates_skipped_already_enriched,
        failedCount: 0,
        total: queued.candidates_total,
        timedOut: false,
      });
      return;
    }

    setStatusText(`Enriching ${queued.candidates_queued}…`);

    pollRef.current = setInterval(async () => {
      if (!mountedRef.current) {
        stopPolling();
        return;
      }
      pollCountRef.current += 1;
      try {
        const scan = await apiRequest<PollScanShape>(
          `/api/v1/email-extractor/scans/${scanId}`,
        );
        onProgress?.();
        const { enriched, failed, total } = countStatuses(scan);
        const wasCancelled = scan.enrich_cancelled_at !== null;

        setStatusText(
          wasCancelled
            ? `Stopping — enriched ${enriched} of ${total}${failed > 0 ? `, ${failed} failed` : ""}…`
            : `Enriched ${enriched} of ${total}${failed > 0 ? `, ${failed} failed` : ""}`,
        );

        if (wasCancelled) {
          stopPolling();
          if (!mountedRef.current) return;
          setIsRunning(false);
          setIsStopping(false);
          setOptimisticQueued(0);
          setStatusText(
            `Stopped — kept ${enriched} of ${total} extracted${failed > 0 ? `, ${failed} failed` : ""}.`,
          );
          onDone?.({
            enrichedCount: enriched,
            failedCount: failed,
            total,
            timedOut: false,
            cancelled: true,
          });
          return;
        }

        if (enriched + failed >= total) {
          stopPolling();
          if (!mountedRef.current) return;
          setIsRunning(false);
          setOptimisticQueued(0);
          setStatusText(
            `Done — enriched ${enriched} of ${total}${failed > 0 ? `, ${failed} failed` : ""}.`,
          );
          onDone?.({ enrichedCount: enriched, failedCount: failed, total, timedOut: false });
          return;
        }

        if (pollCountRef.current >= MAX_POLLS) {
          stopPolling();
          if (!mountedRef.current) return;
          setIsRunning(false);
          setOptimisticQueued(0);
          setStatusText(null);
          toast.info("Still enriching — refresh to see latest.");
          onDone?.({ enrichedCount: enriched, failedCount: failed, total, timedOut: true });
        }
      } catch {
        stopPolling();
        if (!mountedRef.current) return;
        setIsRunning(false);
        setIsStopping(false);
        setOptimisticQueued(0);
        setStatusText(null);
        toast.error("Lost connection while polling — please try again.");
      }
    }, POLL_INTERVAL_MS);
  }, [isRunning, onDone, onProgress, scanId, stopPolling, toast, unenrichedCount]);

  const handleStop = useCallback(async () => {
    if (!isRunning || isStopping) return;
    setIsStopping(true);
    setStatusText("Stopping…");
    try {
      await cancelEnrichAll(scanId);
    } catch {
      if (!mountedRef.current) return;
      setIsStopping(false);
      toast.error("Couldn't stop enrichment — please try again.");
    }
  }, [isRunning, isStopping, scanId, toast]);

  const disabled = isRunning || unenrichedCount <= 0;
  const disabledTitle =
    !isRunning && unenrichedCount <= 0 ? "All discovered emails already enriched" : undefined;
  const queuedForDisplay = isRunning ? optimisticQueued : unenrichedCount;
  const label = isRunning ? `Enriching ${queuedForDisplay}…` : "Enrich All";

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void handleClick()}
          disabled={disabled}
          aria-busy={isRunning}
          title={disabledTitle}
          className="inline-flex items-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-3 py-2 text-xs font-semibold text-white shadow-sm shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)] hover:shadow-md hover:shadow-[var(--accent,#6366f1)]/25 focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isRunning ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Sparkles className="h-4 w-4" aria-hidden />
          )}
          {label}
        </button>
        {isRunning ? (
          <button
            type="button"
            onClick={() => void handleStop()}
            disabled={isStopping}
            aria-label="Stop enrichment"
            title="Stop enrichment — keeps rows already extracted"
            className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-2 text-xs font-semibold text-[var(--text-dim,#475569)] shadow-sm transition hover:bg-[var(--surface-2,#f1f6fd)] focus:outline-none focus:ring-2 focus:ring-[var(--border-2,rgba(30,64,175,0.16))] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Square className="h-4 w-4" aria-hidden />
            {isStopping ? "Stopping…" : "Stop"}
          </button>
        ) : null}
      </div>
      {statusText !== null ? (
        <span className="text-xs text-[var(--text-dim,#475569)]" aria-live="polite">
          {statusText}
        </span>
      ) : null}
    </div>
  );
}
