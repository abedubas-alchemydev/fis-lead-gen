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
    <div className="rounded-[28px] border border-white/80 bg-white/92 p-7 shadow-shell">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-blue">
            {cadence}
          </p>
          <h2 className="text-xl font-semibold text-navy">{pipelineName}</h2>
          <p className="text-sm leading-6 text-slate-600">{description}</p>
          <p className="text-xs text-slate-500">
            Expected duration:{" "}
            <span className="font-medium text-slate-700">{eta}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={cooldown}
          className="inline-flex h-11 items-center rounded-2xl bg-navy px-5 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] hover:shadow-xl hover:shadow-navy/20 disabled:cursor-not-allowed disabled:opacity-60"
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
