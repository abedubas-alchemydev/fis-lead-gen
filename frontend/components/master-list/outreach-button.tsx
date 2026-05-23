"use client";

import { useState } from "react";

import { OutreachModal } from "@/components/master-list/outreach-modal";
import type {
  AdvisorContactItem,
  ExecutiveContactItem,
  InvestorContactItem,
} from "@/lib/types";

// Discriminates which BE endpoint pair the OutreachModal targets when the
// user clicks Generate / Send. Investor support is reserved for symmetry
// with the BE schemas (PR #420) and the parallel API helpers in api.ts —
// no UI surface invokes it yet, but the type stays here so adding one
// later is a no-op change.
export type OutreachEntityKind = "broker_dealer" | "advisor" | "investor";

// Structural overlap is sufficient for the modal: it only reads
// {id, name, title, email}. Each contact item type already carries an
// `id` keyed to its own entity table, so the union is enough — no
// runtime narrowing required.
export type OutreachContact =
  | ExecutiveContactItem
  | AdvisorContactItem
  | InvestorContactItem;

interface OutreachButtonProps {
  entityKind: OutreachEntityKind;
  entityId: number;
  entityName: string;
  contact: OutreachContact;
}

export function OutreachButton({
  entityKind,
  entityId,
  entityName,
  contact,
}: OutreachButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 rounded-full bg-[var(--accent,#6366f1)] px-3 py-1 text-xs font-medium text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
      >
        Outreach
      </button>
      {open ? (
        <OutreachModal
          entityKind={entityKind}
          entityId={entityId}
          entityName={entityName}
          contact={contact}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
