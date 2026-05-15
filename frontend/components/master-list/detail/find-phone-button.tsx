"use client";

import { Loader2, Phone, Sparkles } from "lucide-react";
import { useState } from "react";

import { apiRequest, ApiError } from "@/lib/api";
import type { ExecutiveContactItem } from "@/lib/types";

// Per-row "Find phone" button rendered under a ContactRow when the contact
// has an email but no phone. POSTs to the per-contact Apollo /people/match
// endpoint; one Apollo credit per click. The bd-level Generate-More-Details
// button enriches everyone in one shot, but it can return without a phone
// (Apollo's plan tier doesn't always surface phone). This per-row action is
// the manual retry users reach for when they want phone specifically.
export function FindPhoneButton({
  brokerDealerId,
  contactId,
  onSuccess,
}: {
  brokerDealerId: number;
  contactId: number;
  onSuccess: (updated: ExecutiveContactItem) => void;
}) {
  const [inFlight, setInFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [triedEmpty, setTriedEmpty] = useState(false);

  async function handleClick() {
    setInFlight(true);
    setError(null);
    setTriedEmpty(false);
    try {
      const updated = await apiRequest<ExecutiveContactItem>(
        `/api/v1/broker-dealers/${brokerDealerId}/contacts/${contactId}/find-phone`,
        { method: "POST" },
      );
      onSuccess(updated);
      if (!updated.phone) {
        setTriedEmpty(true);
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.detail || `Request failed (${e.status})`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setInFlight(false);
    }
  }

  if (error) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md bg-[var(--pill-red-bg,#fee2e2)] px-2 py-0.5 text-[11px] text-[var(--pill-red-text,#b91c1c)]">
        {error}
      </span>
    );
  }

  if (triedEmpty) {
    return (
      <span className="inline-flex items-center gap-1 text-[var(--text-muted,#94a3b8)] italic">
        Tried, no phone found
      </span>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={inFlight}
      className="inline-flex items-center gap-1 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-2 py-0.5 text-[11px] text-[var(--text-dim,#475569)] transition hover:border-[var(--accent,#6366f1)] hover:text-[var(--accent,#6366f1)] disabled:cursor-not-allowed disabled:opacity-60"
    >
      {inFlight ? (
        <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
      ) : (
        <Sparkles className="h-3 w-3" strokeWidth={2} />
      )}
      <Phone className="h-3 w-3" strokeWidth={2} />
      {inFlight ? "Looking up…" : "Find phone"}
    </button>
  );
}
