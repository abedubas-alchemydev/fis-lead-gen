"use client";

import { Loader2, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useToast } from "@/components/ui/use-toast";
import { apiRequest } from "@/lib/api";
import { enrichAll, type EnrichAllResponse } from "@/lib/email-extractor";

const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 100;

type PollEnrichmentStatus = "not_enriched" | "enriched" | "no_match" | "error";

interface PollScanShape {
  discovered_emails: Array<{ enrichment_status: PollEnrichmentStatus }>;
}

export interface EnrichAllSummary {
  enrichedCount: number;
  failedCount: number;
  total: number;
  timedOut: boolean;
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
        setStatusText(
          `Enriched ${enriched} of ${total}${failed > 0 ? `, ${failed} failed` : ""}`,
        );

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
        setOptimisticQueued(0);
        setStatusText(null);
        toast.error("Lost connection while polling — please try again.");
      }
    }, POLL_INTERVAL_MS);
  }, [isRunning, onDone, onProgress, scanId, stopPolling, toast, unenrichedCount]);

  const disabled = isRunning || unenrichedCount <= 0;
  const disabledTitle =
    !isRunning && unenrichedCount <= 0 ? "All discovered emails already enriched" : undefined;
  const queuedForDisplay = isRunning ? optimisticQueued : unenrichedCount;
  const label = isRunning ? `Enriching ${queuedForDisplay}…` : "Enrich All";

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={disabled}
        aria-busy={isRunning}
        title={disabledTitle}
        className="inline-flex items-center gap-2 rounded-xl bg-navy px-3 py-2 text-xs font-semibold text-white shadow-sm shadow-navy/15 transition hover:bg-[#112b54] hover:shadow-md hover:shadow-navy/20 focus:outline-none focus:ring-2 focus:ring-blue/30 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {isRunning ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Sparkles className="h-4 w-4" aria-hidden />
        )}
        {label}
      </button>
      {statusText !== null ? (
        <span className="text-xs text-slate-600" aria-live="polite">
          {statusText}
        </span>
      ) : null}
    </div>
  );
}
