"use client";

import { OutreachButton } from "@/components/master-list/outreach-button";
import type { ExecutiveContactItem } from "@/lib/types";

// Inline contact summary rendered under each owner/officer card on the
// firm-detail panel. Email/phone open via mailto: / tel:; LinkedIn opens in
// a new tab. The Outreach button is a coming-soon stub (see OutreachButton).
// Returns null when the contact has neither email nor phone so the parent
// doesn't render an empty trailing line.
export function ContactRow({
  contact,
}: {
  contact: Pick<ExecutiveContactItem, "email" | "phone" | "linkedin_url">;
}) {
  if (!contact.email && !contact.phone) return null;
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
      <OutreachButton contact={contact} disabled />
    </div>
  );
}
