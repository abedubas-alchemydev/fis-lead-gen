"use client";

import {
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCcw,
} from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useCallback, useState } from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { ContactPersonRow } from "@/components/outreach-contacts/contact-person-row";
import { Pill } from "@/components/ui/pill";
import {
  listOutreachContactsFirmPersons,
  type OutreachContactPerson,
  type OutreachContactsFirmRow,
} from "@/lib/outreach-contacts";

// DEMO: the 30-day gap-fill cooldown gate was removed so "Enrich all" stays
// always-clickable for the client demo (mirrors the /investors always-enabled
// override). The withinCooldown() helper + GAP_FILL_COOLDOWN_DAYS const that
// disabled the button within the cooldown window were deleted, along with the
// matching BE 429 in api/v1/endpoints/outreach.py (dispatch_firm_gap_fill).
// To restore production gating, bring them back and re-add the
// `|| cooldown.active` gate + the cooldown tooltip branch below.

const ENTITY_KIND_LABEL: Record<OutreachContactsFirmRow["entity_kind"], string> = {
  broker_dealer: "Broker-Dealer",
  advisor: "Investment Advisor",
  institutional_investor: "Institutional Investor",
};

function firmProfileHref(firm: OutreachContactsFirmRow): Route {
  switch (firm.entity_kind) {
    case "broker_dealer":
      return `/master-list/${firm.entity_id}` as Route;
    case "advisor":
      return `/advisor-list/${firm.entity_id}` as Route;
    case "institutional_investor":
      // entity_id is the INVESTOR id; this page resolves investor->advisor
      // server-side and forwards. Do not point straight at /advisor-list.
      return `/institutional-investors/${firm.entity_id}` as Route;
  }
}

interface FirmRowProps {
  firm: OutreachContactsFirmRow;
  isEnriching: boolean;
  enrichNotice: string | null;
  enrichError: string | null;
  onEnrich: (firm: OutreachContactsFirmRow) => void;
}

export function FirmRow({
  firm,
  isEnriching,
  enrichNotice,
  enrichError,
  onEnrich,
}: FirmRowProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const [persons, setPersons] = useState<OutreachContactPerson[] | null>(null);
  const [personsLoading, setPersonsLoading] = useState(false);
  const [personsError, setPersonsError] = useState<string | null>(null);

  const loadPersons = useCallback(async () => {
    setPersonsLoading(true);
    setPersonsError(null);
    try {
      const response = await listOutreachContactsFirmPersons(
        firm.entity_kind,
        firm.entity_id,
      );
      setPersons(response.items);
    } catch (err) {
      setPersonsError(
        err instanceof Error ? err.message : "Could not load contacts",
      );
    } finally {
      setPersonsLoading(false);
    }
  }, [firm.entity_kind, firm.entity_id]);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next && persons === null && !personsLoading) {
        void loadPersons();
      }
      return next;
    });
  }, [persons, personsLoading, loadPersons]);

  // DEMO: cooldown gating removed (see top-of-file note) -- only the live
  // in-flight / enriching states still disable the button.
  const enrichDisabled = isEnriching || firm.gap_fill_in_progress;
  const buttonTitle = firm.gap_fill_in_progress
    ? "Gap-fill already running for this firm."
    : "Re-enrich missing emails, phones, and LinkedIn URLs.";

  return (
    <div className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0">
      <div className="flex flex-wrap items-start gap-3">
        <button
          type="button"
          onClick={handleToggle}
          className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
          aria-label={expanded ? "Collapse contacts" : "Expand contacts"}
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" strokeWidth={2} />
          ) : (
            <ChevronRight className="h-4 w-4" strokeWidth={2} />
          )}
        </button>
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <Pill variant="unknown">{ENTITY_KIND_LABEL[firm.entity_kind]}</Pill>
            {firm.gap_fill_in_progress ? (
              <Pill variant="info">Gap-fill running</Pill>
            ) : null}
          </div>
          <Link
            href={firmProfileHref(firm)}
            className="mb-1 block text-left text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
          >
            {firm.name}
          </Link>
          <p className="text-[12px] leading-5 text-[var(--text-dim,#475569)]">
            <span className="tabular-nums">{firm.contact_count.toLocaleString()}</span>{" "}
            contact{firm.contact_count === 1 ? "" : "s"}
            {" · "}
            <span className="tabular-nums">{firm.with_email_count.toLocaleString()}</span>{" "}
            with email
            {" · "}
            <span className="tabular-nums">{firm.with_phone_count.toLocaleString()}</span>{" "}
            with phone
          </p>
        </div>
        <button
          type="button"
          onClick={() => onEnrich(firm)}
          disabled={enrichDisabled}
          title={buttonTitle}
          className={clsx(
            buttonBase,
            buttonSizes.sm,
            "shrink-0 border border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.05)] text-[#6366f1] hover:bg-[rgba(99,102,241,0.12)]",
          )}
        >
          {isEnriching || firm.gap_fill_in_progress ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : (
            <RefreshCcw className="h-3.5 w-3.5" strokeWidth={2} />
          )}
          {isEnriching || firm.gap_fill_in_progress ? "Enriching…" : "Enrich all"}
        </button>
      </div>

      {enrichNotice ? (
        <p className="ml-10 mt-2 rounded-md border border-[rgba(99,102,241,0.2)] bg-[rgba(99,102,241,0.05)] px-3 py-1.5 text-[12px] text-[var(--text-dim,#475569)]">
          {enrichNotice}
        </p>
      ) : null}
      {enrichError ? (
        <p className="ml-10 mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-[12px] text-red-700">
          {enrichError}
        </p>
      ) : null}

      {expanded ? (
        <div className="ml-10 mt-3">
          {personsLoading ? (
            <div className="flex items-center gap-2 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
              Loading contacts…
            </div>
          ) : personsError ? (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
              {personsError}
            </p>
          ) : persons === null ? null : persons.length === 0 ? (
            <p className="py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              No contacts on file.
            </p>
          ) : (
            <ul className="space-y-2">
              {persons.map((person) => (
                <ContactPersonRow
                  key={person.contact_id}
                  person={person}
                  firm={firm}
                />
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
