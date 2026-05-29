"use client";

import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Mail,
  Phone,
  RefreshCcw,
} from "lucide-react";
import { useCallback, useState } from "react";

import { Pill } from "@/components/ui/pill";
import {
  listOutreachContactsFirmPersons,
  type OutreachContactPerson,
  type OutreachContactsFirmRow,
} from "@/lib/outreach-contacts";

// Hard-coded 30-day cooldown -- matches GAP_FILL_COOLDOWN_DAYS on the
// backend dispatch endpoint. Used to render a disabled state on the
// Enrich button when the firm is within the cooldown window.
const GAP_FILL_COOLDOWN_DAYS = 30;

const ENTITY_KIND_LABEL: Record<OutreachContactsFirmRow["entity_kind"], string> = {
  broker_dealer: "Broker-Dealer",
  advisor: "Investment Advisor",
  institutional_investor: "Institutional Investor",
};

function withinCooldown(stamp: string | null): { active: boolean; daysLeft: number } {
  if (!stamp) return { active: false, daysLeft: 0 };
  const last = Date.parse(stamp);
  if (Number.isNaN(last)) return { active: false, daysLeft: 0 };
  const elapsedDays = (Date.now() - last) / 86_400_000;
  if (elapsedDays >= GAP_FILL_COOLDOWN_DAYS) return { active: false, daysLeft: 0 };
  return { active: true, daysLeft: Math.max(1, Math.ceil(GAP_FILL_COOLDOWN_DAYS - elapsedDays)) };
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

  const cooldown = withinCooldown(firm.last_gap_fill_attempt_at);
  const enrichDisabled = isEnriching || firm.gap_fill_in_progress || cooldown.active;
  const buttonTitle = firm.gap_fill_in_progress
    ? "Gap-fill already running for this firm."
    : cooldown.active
      ? `Cooldown active. Try again in ${cooldown.daysLeft} day(s).`
      : "Re-query PDL / Hunter / Snov for missing emails, phones, and LinkedIn URLs.";

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
          <button
            type="button"
            onClick={handleToggle}
            className="mb-1 block text-left text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
          >
            {firm.name}
          </button>
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
          className="inline-flex h-[32px] shrink-0 items-center gap-1.5 rounded-md border border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.05)] px-3 text-[12px] font-semibold text-[#6366f1] transition hover:bg-[rgba(99,102,241,0.12)] disabled:cursor-not-allowed disabled:opacity-50"
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
                <li
                  key={person.contact_id}
                  className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2"
                >
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
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[var(--text-dim,#475569)]">
                    {person.email ? (
                      <span className="inline-flex items-center gap-1">
                        <Mail className="h-3 w-3" strokeWidth={2} />
                        <a
                          href={`mailto:${person.email}`}
                          className="hover:text-[#6366f1]"
                        >
                          {person.email}
                        </a>
                      </span>
                    ) : null}
                    {person.phone ? (
                      <span className="inline-flex items-center gap-1">
                        <Phone className="h-3 w-3" strokeWidth={2} />
                        <a
                          href={`tel:${person.phone}`}
                          className="hover:text-[#6366f1]"
                        >
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
                    {!person.email && !person.phone && !person.linkedin_url ? (
                      <span className="italic text-[var(--text-muted,#94a3b8)]">
                        No channels on file
                      </span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
