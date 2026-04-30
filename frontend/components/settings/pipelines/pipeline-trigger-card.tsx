"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import type { PipelineTriggerResponse } from "@/lib/types";
import { useToast } from "@/components/ui/use-toast";

import { ConfirmTriggerDialog } from "./confirm-trigger-dialog";

const COOLDOWN_MS = 5_000;

interface PipelineTriggerCardProps {
  pipelineName: string;
  description: string;
  cadence: string;
  eta: string;
  // Defer action invocation to the card so the parent doesn't need to
  // know which API helper goes with which card. Returns the BE-issued
  // run_id which we surface in the success toast for audit/log lookup.
  runAction: () => Promise<PipelineTriggerResponse>;
  // Optional callback so the parent can refresh the recent-runs table
  // after a successful trigger without coupling the card to that table.
  onSuccess?: () => void;
}

export function PipelineTriggerCard({
  pipelineName,
  description,
  cadence,
  eta,
  runAction,
  onSuccess,
}: PipelineTriggerCardProps) {
  const [confirming, setConfirming] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const cooldownTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toast = useToast();

  useEffect(() => {
    return () => {
      if (cooldownTimer.current) {
        clearTimeout(cooldownTimer.current);
      }
    };
  }, []);

  async function handleConfirm() {
    try {
      const response = await runAction();
      toast.success(
        `Pipeline started — run #${response.run_id} (${response.status}).`,
        { title: pipelineName },
      );
      setConfirming(false);
      setCooldown(true);
      cooldownTimer.current = setTimeout(() => {
        setCooldown(false);
      }, COOLDOWN_MS);
      onSuccess?.();
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.detail
          : error instanceof Error
            ? error.message
            : "Pipeline trigger failed.";
      toast.error(message, { title: pipelineName });
      setConfirming(false);
    }
  }

  return (
    <div className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            {cadence}
          </p>
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            {pipelineName}
          </h2>
          <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            {description}
          </p>
          <p className="text-xs text-[var(--text-muted,#94a3b8)]">
            Expected duration:{" "}
            <span className="font-medium text-[var(--text-dim,#475569)]">
              {eta}
            </span>
          </p>
        </div>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={cooldown}
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none sm:w-auto"
        >
          {cooldown ? "Started" : "Run now"}
        </button>
      </div>
      {confirming ? (
        <ConfirmTriggerDialog
          pipelineName={pipelineName}
          eta={eta}
          onCancel={() => setConfirming(false)}
          onConfirm={handleConfirm}
        />
      ) : null}
    </div>
  );
}
