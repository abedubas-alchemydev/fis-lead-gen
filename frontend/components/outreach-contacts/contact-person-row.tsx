"use client";

import { ExternalLink, Mail, MailPlus, Phone } from "lucide-react";
import { useState } from "react";

import type { OutreachEntityKind as ModalEntityKind } from "@/components/master-list/outreach-button";
import { OutreachModal } from "@/components/master-list/outreach-modal";
import type {
  OutreachContactPerson,
  OutreachContactsFirmRow,
} from "@/lib/outreach-contacts";

// The contacts page's entity_kind ("institutional_investor") differs from the
// modal's discriminator ("investor"); the other two kinds match 1:1. The
// per-firm draft/send endpoints resolve contact_id against the same table the
// /persons endpoint pulled it from, so the typed (non-adhoc) path works
// directly -- full personalization + FK-stamped audit rows.
const OUTREACH_KIND: Record<OutreachContactsFirmRow["entity_kind"], ModalEntityKind> = {
  broker_dealer: "broker_dealer",
  advisor: "advisor",
  institutional_investor: "investor",
};

interface ContactPersonRowProps {
  person: OutreachContactPerson;
  firm: OutreachContactsFirmRow;
}

export function ContactPersonRow({
  person,
  firm,
}: ContactPersonRowProps): React.ReactElement {
  const [outreachOpen, setOutreachOpen] = useState(false);

  return (
    <li className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
          {person.name || "(no name)"}
        </span>
        {person.title ? (
          <span className="text-[12px] text-[var(--text-dim,#475569)]">
            {person.title}
          </span>
        ) : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--text-dim,#475569)]">
        {person.email ? (
          <span className="inline-flex items-center gap-1">
            <Mail className="h-3 w-3" strokeWidth={2} />
            <button
              type="button"
              onClick={() => setOutreachOpen(true)}
              title="Compose outreach"
              className="cursor-pointer hover:text-[#6366f1]"
            >
              {person.email}
            </button>
          </span>
        ) : null}
        {person.phone ? (
          <span className="inline-flex items-center gap-1">
            <Phone className="h-3 w-3" strokeWidth={2} />
            <a href={`tel:${person.phone}`} className="hover:text-[#6366f1]">
              {person.phone}
            </a>
          </span>
        ) : null}
        {person.linkedin_url ? (
          <a
            href={person.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 hover:text-[#6366f1]"
          >
            <ExternalLink className="h-3 w-3" strokeWidth={2} />
            LinkedIn
          </a>
        ) : null}
        {person.email ? (
          <button
            type="button"
            onClick={() => setOutreachOpen(true)}
            className="ml-auto inline-flex items-center gap-1 rounded-md bg-[var(--accent,#6366f1)] px-2 py-0.5 text-[11px] font-semibold text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
          >
            <MailPlus className="h-3 w-3" strokeWidth={2} aria-hidden />
            Outreach
          </button>
        ) : null}
        {!person.email && !person.phone && !person.linkedin_url ? (
          <span className="italic text-[var(--text-muted,#94a3b8)]">
            No channels on file
          </span>
        ) : null}
      </div>

      {outreachOpen && person.email ? (
        <OutreachModal
          entityKind={OUTREACH_KIND[firm.entity_kind]}
          entityId={firm.entity_id}
          entityName={firm.name}
          contact={{
            id: person.contact_id,
            name: person.name,
            title: person.title ?? "",
            email: person.email,
          }}
          onClose={() => setOutreachOpen(false)}
        />
      ) : null}
    </li>
  );
}
