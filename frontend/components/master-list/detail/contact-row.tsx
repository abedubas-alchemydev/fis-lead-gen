"use client";

import { FindPhoneButton } from "@/components/master-list/detail/find-phone-button";
import { OutreachButton } from "@/components/master-list/outreach-button";
import type { ExecutiveContactItem } from "@/lib/types";

// Inline contact summary rendered under each owner/officer card on the
// firm-detail panel. Email/phone open via mailto: / tel:; LinkedIn opens in
// a new tab. The Outreach button opens the cold-email draft modal (Gemini
// Flash) when the contact has an email — without one there's nothing to
// address, so we hide the button. The Find-phone button is shown when the
// contact has an email but no phone (the common case post-Apollo enrichment
// on our current plan) — clicking it costs one Apollo credit. Returns null
// only when the contact has nothing renderable: no email, no phone, no
// LinkedIn (rare; mostly FINRA-only officers with no provider match).
export function ContactRow({
  brokerDealerId,
  brokerDealerName,
  contact,
  onContactUpdated,
}: {
  brokerDealerId: number;
  brokerDealerName: string;
  contact: ExecutiveContactItem;
  onContactUpdated?: (updated: ExecutiveContactItem) => void;
}) {
  if (!contact.email && !contact.phone && !contact.linkedin_url) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      {contact.email ? (
        <a
          href={`mailto:${contact.email}`}
          className="text-[var(--accent,#6366f1)] transition hover:underline"
        >
          {contact.email}
        </a>
      ) : null}
      {contact.phone ? (
        <a
          href={`tel:${contact.phone}`}
          className="text-[var(--text-dim,#475569)] transition hover:underline"
        >
          {contact.phone}
        </a>
      ) : contact.email ? (
        <>
          <span className="text-[var(--text-muted,#94a3b8)] italic">
            Phone unavailable
          </span>
          {onContactUpdated ? (
            <FindPhoneButton
              brokerDealerId={brokerDealerId}
              contactId={contact.id}
              onSuccess={onContactUpdated}
            />
          ) : null}
        </>
      ) : null}
      {contact.linkedin_url ? (
        <a
          href={contact.linkedin_url}
          target="_blank"
          rel="noreferrer"
          className="text-[var(--accent,#6366f1)] transition hover:underline"
        >
          LinkedIn
        </a>
      ) : null}
      {contact.email ? (
        <span className="ml-auto">
          <OutreachButton
            brokerDealerId={brokerDealerId}
            brokerDealerName={brokerDealerName}
            contact={contact}
          />
        </span>
      ) : null}
    </div>
  );
}
