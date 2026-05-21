"use client";

import { FindPhoneButton } from "@/components/master-list/detail/find-phone-button";
import { OutreachButton } from "@/components/master-list/outreach-button";
import type {
  ExecutiveContactItem,
  InvestorContactItem,
} from "@/lib/types";

// Generic contact row used on both /master-list/{id} (broker-dealer) and
// /institutional-investors/{id} (institutional investor) detail pages.
// Renders the new multi-value emails[] / phones[] arrays with type chips
// (personal / mobile) when PDL returned multiples; falls back to a 1-
// element synthesized list for single-value sources (Apollo, FOCUS PDF,
// Hunter, Snov) via the schema's read-time validator. The Find-phone
// button shows when there's an email but no phones; POSTs to the right
// endpoint via entityKind. Outreach (cold-email modal) stays BD-only --
// there's no outreach flow for institutional investors yet.
export function ContactRow<
  T extends ExecutiveContactItem | InvestorContactItem,
>({
  entityKind = "broker-dealer",
  entityId,
  entityName,
  contact,
  onContactUpdated,
}: {
  entityKind?: "broker-dealer" | "investor";
  entityId: number;
  entityName: string;
  contact: T;
  onContactUpdated?: (updated: T) => void;
}) {
  const emails = contact.emails ?? [];
  const phones = contact.phones ?? [];
  if (emails.length === 0 && phones.length === 0 && !contact.linkedin_url) {
    return null;
  }
  const showFindPhone =
    phones.length === 0 && !!contact.email && !!onContactUpdated;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      {emails.map((email, i) => (
        <span key={`email-${i}`} className="inline-flex items-center gap-1">
          <a
            href={`mailto:${email.value}`}
            className="text-[var(--accent,#6366f1)] transition hover:underline"
          >
            {email.value}
          </a>
          {email.type === "personal" ? (
            <span className="rounded-full bg-[var(--surface-2,#f1f6fd)] px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              personal
            </span>
          ) : null}
        </span>
      ))}
      {phones.map((phone, i) => (
        <span key={`phone-${i}`} className="inline-flex items-center gap-1">
          <a
            href={`tel:${phone.value}`}
            className="text-[var(--text-dim,#475569)] transition hover:underline"
          >
            {phone.value}
          </a>
          {phone.type !== "work" ? (
            <span className="rounded-full bg-[var(--surface-2,#f1f6fd)] px-1.5 py-0.5 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              {phone.type}
            </span>
          ) : null}
        </span>
      ))}
      {showFindPhone ? (
        <>
          <span className="text-[var(--text-muted,#94a3b8)] italic">
            Phone unavailable
          </span>
          <FindPhoneButton
            entityKind={entityKind}
            entityId={entityId}
            contactId={contact.id}
            onSuccess={onContactUpdated!}
          />
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
      {entityKind === "broker-dealer" && contact.email ? (
        <span className="ml-auto">
          <OutreachButton
            brokerDealerId={entityId}
            brokerDealerName={entityName}
            contact={contact as unknown as ExecutiveContactItem}
          />
        </span>
      ) : null}
    </div>
  );
}
