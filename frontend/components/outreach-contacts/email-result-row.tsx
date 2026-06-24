"use client";

import { Mail, MailPlus, Phone, ScanLine } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useState } from "react";

import type { OutreachEntityKind as ModalEntityKind } from "@/components/master-list/outreach-button";
import { OutreachModal } from "@/components/master-list/outreach-modal";
import { Pill } from "@/components/ui/pill";
import type {
  EmailSearchResult,
  OutreachEntityKind,
} from "@/lib/outreach-contacts";

// One flat email row rendered in the contacts page "email mode" (when the
// user searches by an email address). Email is the prominent field; the
// row also carries who it is (owner_name + title), which firm it belongs to
// (firm_name + entity-kind chip), an optional phone, and an "Outreach"
// action. The Outreach wiring reuses the SAME modal the firm-row sub-lists
// use: a typed contact (source="contact") goes through the firm-typed
// draft/send path; a discovered email (source="extracted") goes through the
// adhoc compose path -- exactly mirroring ContactPersonRow and
// DiscoveredEmailRow respectively.

// Short entity-kind chip label for the email-mode row. "Investor" is the
// compact form of "institutional_investor" the brief asks for here (the
// firm-card path spells it "Institutional Investor"; this flat list keeps
// it tight).
const ENTITY_KIND_CHIP: Record<OutreachEntityKind, string> = {
  broker_dealer: "Broker-Dealer",
  advisor: "Advisor",
  institutional_investor: "Investor",
};

// The contacts page's entity_kind ("institutional_investor") differs from
// the modal's discriminator ("investor"); the other two match 1:1. Same
// mapping ContactPersonRow uses for the typed (non-adhoc) Outreach path.
const OUTREACH_KIND: Record<OutreachEntityKind, ModalEntityKind> = {
  broker_dealer: "broker_dealer",
  advisor: "advisor",
  institutional_investor: "investor",
};

function firmProfileHref(row: EmailSearchResult): Route {
  switch (row.entity_kind) {
    case "broker_dealer":
      return `/master-list/${row.entity_id}` as Route;
    case "advisor":
      return `/advisor-list/${row.entity_id}` as Route;
    case "institutional_investor":
      // entity_id is the INVESTOR id; the page resolves investor->advisor
      // server-side and forwards. Do not point straight at /advisor-list.
      return `/institutional-investors/${row.entity_id}` as Route;
  }
}

interface EmailResultRowProps {
  row: EmailSearchResult;
}

export function EmailResultRow({ row }: EmailResultRowProps): React.ReactElement {
  const [outreachOpen, setOutreachOpen] = useState(false);

  const isExtracted = row.source === "extracted";

  return (
    <li className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2.5">
      {/* Email — the prominent field for this mode */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <Mail
            className="h-3.5 w-3.5 shrink-0 text-[var(--accent,#6366f1)]"
            strokeWidth={2}
            aria-hidden
          />
          <button
            type="button"
            onClick={() => setOutreachOpen(true)}
            title="Compose outreach"
            className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
          >
            {row.email}
          </button>
        </span>
        {isExtracted ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-[2px] text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            <ScanLine className="h-3 w-3" strokeWidth={2} aria-hidden />
            Extracted
          </span>
        ) : (
          <Pill variant="healthy">Enriched</Pill>
        )}
        <button
          type="button"
          onClick={() => setOutreachOpen(true)}
          className="ml-auto inline-flex items-center gap-1 rounded-md bg-[var(--accent,#6366f1)] px-2 py-0.5 text-[11px] font-semibold text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
        >
          <MailPlus className="h-3 w-3" strokeWidth={2} aria-hidden />
          Outreach
        </button>
      </div>

      {/* Who it is */}
      {row.owner_name || row.title ? (
        <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          {row.owner_name ? (
            <span className="text-[12px] font-medium text-[var(--text-dim,#475569)]">
              {row.owner_name}
            </span>
          ) : null}
          {row.title ? (
            <span className="text-[12px] text-[var(--text-muted,#94a3b8)]">
              {row.title}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Which firm + phone */}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-dim,#475569)]">
        <Pill variant="unknown">{ENTITY_KIND_CHIP[row.entity_kind]}</Pill>
        <Link
          href={firmProfileHref(row)}
          className="font-medium text-[var(--text-dim,#475569)] transition hover:text-[#6366f1]"
        >
          {row.firm_name || "(unknown firm)"}
        </Link>
        {row.phone ? (
          <span className="inline-flex items-center gap-1">
            <Phone className="h-3 w-3" strokeWidth={2} />
            <a href={`tel:${row.phone}`} className="hover:text-[#6366f1]">
              {row.phone}
            </a>
          </span>
        ) : null}
      </div>

      {outreachOpen ? (
        isExtracted ? (
          // Discovered email: no firm/contact FK -- address by email via the
          // adhoc compose path (same as DiscoveredEmailRow).
          <OutreachModal
            entityKind="adhoc"
            entityId={0}
            entityName=""
            contact={{
              id: row.contact_id ?? 0,
              name: row.owner_name ?? "",
              title: row.title ?? "",
              email: row.email,
            }}
            onClose={() => setOutreachOpen(false)}
          />
        ) : (
          // Typed contact: full firm + contact context so the per-firm
          // draft/send endpoints personalize + FK-stamp the audit row
          // (same as ContactPersonRow).
          <OutreachModal
            entityKind={OUTREACH_KIND[row.entity_kind]}
            entityId={row.entity_id}
            entityName={row.firm_name}
            contact={{
              id: row.contact_id ?? 0,
              name: row.owner_name ?? "",
              title: row.title ?? "",
              email: row.email,
            }}
            onClose={() => setOutreachOpen(false)}
          />
        )
      ) : null}
    </li>
  );
}
