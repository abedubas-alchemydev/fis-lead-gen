"use client";

import { useState } from "react";

import { OutreachModal } from "@/components/master-list/outreach-modal";
import type {
  AdvisorContactItem,
  ExecutiveContactItem,
  InvestorContactItem,
} from "@/lib/types";

// Discriminates which BE endpoint pair the OutreachModal targets when the
// user clicks Generate / Send. "broker_dealer" / "advisor" / "investor"
// hit the per-firm draft+send endpoints (firm id + contact id). "adhoc"
// hits the free-form /outreach/adhoc-* pair, addressing the recipient by
// email alone — used by the /investors insider feed, whose rows are Form 4
// reporting persons with no firm/contact FK.
export type OutreachEntityKind =
  | "broker_dealer"
  | "advisor"
  | "investor"
  | "adhoc";

// Minimal recipient shape for the adhoc path. The Form 4 insider feed has
// no contact-table row — the email comes from per-row enrichment — so we
// synthesize one of these. `id` is unused on the adhoc send (no contact
// FK) but stays a required number so the firm branches keep `contact.id`
// typed as number; `title` stays a plain string for the same reason.
export type AdhocOutreachContact = {
  id: number;
  name: string;
  title: string;
  email: string | null;
};

// Structural overlap is sufficient for the modal: it only reads
// {id, name, title, email}. Each contact item type already carries an
// `id` keyed to its own entity table, so the union is enough — no
// runtime narrowing required.
export type OutreachContact =
  | ExecutiveContactItem
  | AdvisorContactItem
  | InvestorContactItem
  | AdhocOutreachContact;

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
