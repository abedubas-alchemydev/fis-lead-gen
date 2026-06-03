"use client";

import { Loader2, Phone, Sparkles } from "lucide-react";
import { useState } from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { apiRequest } from "@/lib/api";
import type {
  ExecutiveContactItem,
  InvestorContactItem,
} from "@/lib/types";

// Per-row "Find phone" button rendered under a ContactRow when the contact
// has an email but no phone. POSTs to the per-contact PDL+Apollo
// /find-phone endpoint; PDL is tried first now (PR 2) with Apollo as a
// fallback. Generic over the contact item type so the same button works
// on both the broker-dealer detail page (/master-list/{id}) and the
// institutional-investor detail page (/institutional-investors/{id}) --
// entityKind picks the URL base.
export function FindPhoneButton<
  T extends ExecutiveContactItem | InvestorContactItem,
>({
  entityKind = "broker-dealer",
  entityId,
  contactId,
  onSuccess,
}: {
  entityKind?: "broker-dealer" | "investor";
  entityId: number;
  contactId: number;
  onSuccess: (updated: T) => void;
}) {
  const [inFlight, setInFlight] = useState(false);

  const basePath =
    entityKind === "investor" ? "institutional-investors" : "broker-dealers";

  async function handleClick() {
    setInFlight(true);
    try {
      const updated = await apiRequest<T>(
        `/api/v1/${basePath}/${entityId}/contacts/${contactId}/find-phone`,
        { method: "POST" },
      );
      onSuccess(updated);
    } catch {
      // CLIENT/DEMO: swallow lookup errors / empty results — no negative
      // "Tried, no phone found" or red error pill. The button just returns to
      // its ready state so it can be retried.
    } finally {
      setInFlight(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={inFlight}
      className={clsx(
        buttonBase,
        buttonSizes.sm,
        "gap-1 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-2 text-[11px] font-medium text-[var(--text-dim,#475569)] hover:border-[var(--accent,#6366f1)] hover:text-[var(--accent,#6366f1)]",
      )}
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
