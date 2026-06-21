"use client";

import {
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCcw,
} from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import clsx from "clsx";

import { buttonBase, buttonSizes } from "@/components/ui/button";
import { ContactPersonRow } from "@/components/outreach-contacts/contact-person-row";
import { DiscoveredEmailRow } from "@/components/outreach-contacts/discovered-email-row";
import { Pill } from "@/components/ui/pill";
import {
  listOutreachContactsFirmDiscoveredEmails,
  listOutreachContactsFirmPersons,
  type DiscoveredContactRow,
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

// Rows shown per page for each expanded sub-list (typed contacts + extracted
// emails). Long firms (e.g. J.P. Morgan = 200 extracted emails) would otherwise
// render every row into the DOM at once; slicing to a page keeps it light.
const FIRM_SUBLIST_PAGE_SIZE = 10;

// Lightweight inline pager for an in-memory sub-list. Mirrors the "Showing X–Y
// of N" + Prev/Next style of the page-level firm-list control in
// outreach-contacts-client.tsx. The caller only renders this when total > the
// page size, so it never appears for short lists.
function SubListPagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}): React.ReactElement {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const start = (safePage - 1) * pageSize + 1;
  const end = Math.min(safePage * pageSize, total);

  return (
    <div className="mt-2 flex items-center justify-between gap-3 text-[12px]">
      <span className="text-[var(--text-muted,#94a3b8)]">
        Showing <span className="tabular-nums">{start}</span>–
        <span className="tabular-nums">{end}</span> of{" "}
        <span className="tabular-nums">{total.toLocaleString()}</span>
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, safePage - 1))}
          disabled={safePage <= 1}
          className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
        >
          Prev
        </button>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, safePage + 1))}
          disabled={safePage >= totalPages}
          className="inline-flex items-center rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-transparent px-3 py-1 text-[12px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
        >
          Next
        </button>
      </div>
    </div>
  );
}

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

  // Discovered (Email-Extractor) emails -- lazy-loaded on expand, same
  // loading/error affordance as the typed-contacts list. Only fetched when the
  // firm actually has any (discovered_email_count > 0).
  const [discovered, setDiscovered] = useState<DiscoveredContactRow[] | null>(
    null,
  );
  const [discoveredLoading, setDiscoveredLoading] = useState(false);
  const [discoveredError, setDiscoveredError] = useState<string | null>(null);

  // Each sub-list paginates independently (10/page). Page state resets to 1
  // when the list's data (re)loads or the firm is collapsed, so every fresh
  // expand starts on page 1.
  const [personsPage, setPersonsPage] = useState(1);
  const [discoveredPage, setDiscoveredPage] = useState(1);

  const hasDiscovered = firm.discovered_email_count > 0;

  const loadPersons = useCallback(async () => {
    setPersonsLoading(true);
    setPersonsError(null);
    try {
      const response = await listOutreachContactsFirmPersons(
        firm.entity_kind,
        firm.entity_id,
      );
      setPersons(response.items);
      setPersonsPage(1);
    } catch (err) {
      setPersonsError(
        err instanceof Error ? err.message : "Could not load contacts",
      );
    } finally {
      setPersonsLoading(false);
    }
  }, [firm.entity_kind, firm.entity_id]);

  const loadDiscovered = useCallback(async () => {
    setDiscoveredLoading(true);
    setDiscoveredError(null);
    try {
      // Reuse the firm row's entity_kind -- the discovered-emails endpoint
      // keys off the same value the persons endpoint does.
      const response = await listOutreachContactsFirmDiscoveredEmails(
        firm.entity_kind,
        firm.entity_id,
      );
      setDiscovered(response);
      setDiscoveredPage(1);
    } catch (err) {
      setDiscoveredError(
        err instanceof Error ? err.message : "Could not load extracted emails",
      );
    } finally {
      setDiscoveredLoading(false);
    }
  }, [firm.entity_kind, firm.entity_id]);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next) {
        if (persons === null && !personsLoading) {
          void loadPersons();
        }
        if (hasDiscovered && discovered === null && !discoveredLoading) {
          void loadDiscovered();
        }
      } else {
        // Collapsing: the lists stay cached, so reset their pages here to
        // guarantee a re-expand starts on page 1.
        setPersonsPage(1);
        setDiscoveredPage(1);
      }
      return next;
    });
  }, [
    persons,
    personsLoading,
    loadPersons,
    hasDiscovered,
    discovered,
    discoveredLoading,
    loadDiscovered,
  ]);

  // DEMO: cooldown gating removed (see top-of-file note) -- only the live
  // in-flight / enriching states still disable the button.
  const enrichDisabled = isEnriching || firm.gap_fill_in_progress;
  const buttonTitle = firm.gap_fill_in_progress
    ? "Gap-fill already running for this firm."
    : "Re-enrich missing emails, phones, and LinkedIn URLs.";

  // Current-page slices. The full arrays stay in state (counts/pills keep the
  // total); only ≤FIRM_SUBLIST_PAGE_SIZE rows hit the DOM at a time.
  const personsPageItems = useMemo(() => {
    if (!persons) return [];
    const start = (personsPage - 1) * FIRM_SUBLIST_PAGE_SIZE;
    return persons.slice(start, start + FIRM_SUBLIST_PAGE_SIZE);
  }, [persons, personsPage]);

  const discoveredPageItems = useMemo(() => {
    if (!discovered) return [];
    const start = (discoveredPage - 1) * FIRM_SUBLIST_PAGE_SIZE;
    return discovered.slice(start, start + FIRM_SUBLIST_PAGE_SIZE);
  }, [discovered, discoveredPage]);

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
            {hasDiscovered ? (
              <>
                {" · "}
                <span className="font-medium text-[#4338ca]">
                  <span className="tabular-nums">
                    {firm.discovered_email_count.toLocaleString()}
                  </span>{" "}
                  extracted
                </span>
              </>
            ) : null}
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
        <div className="ml-10 mt-3 space-y-4">
          <div>
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
              // Suppress the empty-state for extracted-only firms -- the
              // "Extracted emails" section below carries the row instead.
              hasDiscovered ? null : (
                <p className="py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
                  No contacts on file.
                </p>
              )
            ) : (
              <>
                <ul className="space-y-2">
                  {personsPageItems.map((person) => (
                    <ContactPersonRow
                      key={person.contact_id}
                      person={person}
                      firm={firm}
                    />
                  ))}
                </ul>
                {persons.length > FIRM_SUBLIST_PAGE_SIZE ? (
                  <SubListPagination
                    page={personsPage}
                    pageSize={FIRM_SUBLIST_PAGE_SIZE}
                    total={persons.length}
                    onPageChange={setPersonsPage}
                  />
                ) : null}
              </>
            )}
          </div>

          {hasDiscovered ? (
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Extracted emails ({firm.discovered_email_count.toLocaleString()})
              </p>
              {discoveredLoading ? (
                <div className="flex items-center gap-2 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
                  <Loader2
                    className="h-3.5 w-3.5 animate-spin"
                    strokeWidth={2}
                  />
                  Loading extracted emails…
                </div>
              ) : discoveredError ? (
                <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                  {discoveredError}
                </p>
              ) : discovered === null ? null : discovered.length === 0 ? (
                <p className="py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
                  No extracted emails on file.
                </p>
              ) : (
                <>
                  <ul className="space-y-2">
                    {discoveredPageItems.map((row) => (
                      <DiscoveredEmailRow key={row.id} row={row} />
                    ))}
                  </ul>
                  {discovered.length > FIRM_SUBLIST_PAGE_SIZE ? (
                    <SubListPagination
                      page={discoveredPage}
                      pageSize={FIRM_SUBLIST_PAGE_SIZE}
                      total={discovered.length}
                      onPageChange={setDiscoveredPage}
                    />
                  ) : null}
                </>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
