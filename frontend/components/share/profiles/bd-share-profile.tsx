"use client";

// Read-only broker-dealer profile for the public DOX Share surface. Pure
// presentational: renders entirely from the trimmed BdSharePayload prop —
// no fetching, no navigation, no auth, no enrichment controls, no lead
// scoring / favorites / alerts / diagnostics / filing history. Section
// layout mirrors the authed /master-list/{id} detail page
// (broker-dealer-detail-client.tsx) minus everything interactive.

import { ArrangementFields } from "@/components/master-list/detail/arrangement-fields";
import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { PeopleTable } from "@/components/master-list/detail/people-table";
import { FinancialTrendChart } from "@/components/master-list/detail/financial-trend-chart";
import {
  clearingTypeLabel,
  clearingTypeVariant,
  healthLabel,
  healthVariant,
} from "@/components/master-list/detail/pill-helpers";
import { nameMatches } from "@/components/master-list/detail/name-matching";
import { agencyLabel } from "@/components/master-list/detail/clearing-membership-helpers";
import { SectionPanel } from "@/components/ui/section-panel";
import { Pill } from "@/components/ui/pill";
import { CollapsiblePillList } from "@/components/ui/collapsible-pill-list";
import { parseArrangementBlob } from "@/lib/arrangements";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type { ExecutiveContactItem } from "@/lib/types";

import { Copyable } from "@/components/share/profiles/shared/copyable";
import { DiscoveredEmailsSection } from "@/components/share/profiles/shared/discovered-emails-section";
import { MiniStat } from "@/components/share/profiles/shared/mini-stat";
import { ProfileHeader } from "@/components/share/profiles/shared/profile-header";
import type { BdShareProfileProps, PublicContactItem } from "@/components/share/profiles/types";

// The share payload deliberately omits DB ids, but ChannelIconCell's contact
// prop structurally requires an `id` (never read at runtime) and the
// matched-contact bookkeeping below needs a stable per-row key — synthesize
// one from the row index.
type ContactRow = PublicContactItem & { id: number };

// A discovered-email scan hit lifted onto the same row shape the people
// tables render. ChannelIconCell falls back to the scalar email/phone when
// the multi-value arrays are absent, so no EmailHit/PhoneHit synthesis is
// needed.
type DiscoveredRow = {
  id: number;
  name: string;
  title: string | null;
  email: string;
  phone: string | null;
  linkedin_url: string | null;
};

// nameMatches expects the authed ExecutiveContactItem shape but only reads
// `name` and `title` (org-level check + Apollo name parse); pad the
// remaining fields so the call stays cast-free. The padding values are
// never read.
function toMatchable(row: ContactRow): ExecutiveContactItem {
  return {
    id: row.id,
    bd_id: 0,
    name: row.name,
    title: row.title ?? "",
    email: row.email,
    phone: row.phone,
    linkedin_url: row.linkedin_url,
    source: "sec",
    enriched_at: "",
    emails: row.emails,
    phones: row.phones,
  };
}

export function BdShareProfile({ data }: BdShareProfileProps) {
  const location = [data.city, data.state].filter(Boolean).join(", ");

  // Derived contact-matching for the People panel — mirrors the authed
  // detail page: FINRA owner/officer names are matched against enriched
  // contacts via nameMatches; matched contacts render inline on their
  // officer row and everything unmatched falls to "Additional contacts".
  const contactRows: ContactRow[] = data.executive_contacts.map(
    (contact, index) => ({ ...contact, id: index }),
  );
  const finraNames = [
    ...(data.direct_owners ?? []).map((o) => o.name),
    ...(data.executive_officers ?? []).map((o) => o.name),
  ];
  const matchedContactIds = new Set<number>();
  for (const row of contactRows) {
    if (finraNames.some((n) => nameMatches(n, toMatchable(row)))) {
      matchedContactIds.add(row.id);
    }
  }
  const matchForFinra = (finraDisplay: string): ContactRow | undefined =>
    contactRows.find((row) => nameMatches(finraDisplay, toMatchable(row)));
  const additionalContacts = contactRows.filter(
    (row) => !matchedContactIds.has(row.id),
  );

  // Discovered-email scan hits render in their own full-width section below
  // the grid (see DiscoveredEmailsSection) rather than being folded into the
  // Additional contacts table, where 100+ hits crowd out the real contacts.
  const discoveredRows: DiscoveredRow[] = data.discovered_emails.map(
    (row, index) => ({
      id: contactRows.length + index,
      name: row.name ?? row.email,
      title: row.title,
      email: row.email,
      phone: row.phone,
      linkedin_url: row.linkedin_url,
    }),
  );

  const hasPeople =
    (data.direct_owners?.length ?? 0) > 0 ||
    (data.executive_officers?.length ?? 0) > 0 ||
    additionalContacts.length > 0;

  // Net-capital trend points, oldest → newest. Mirrors the chartPoints
  // memo in broker-dealer-detail-client.tsx.
  const chartPoints = data.financials
    .slice()
    .reverse()
    .map((item) => ({
      label: new Date(item.report_date).getFullYear().toString(),
      value: item.net_capital,
    }));

  return (
    <div className="min-w-0 animate-fade-in">
      <ProfileHeader
        name={data.name}
        website={data.website}
        pills={
          <>
            <Pill variant={healthVariant(data.health_status)}>
              {healthLabel(data.health_status)}
            </Pill>
            <Pill variant={clearingTypeVariant(data.current_clearing_type)}>
              {clearingTypeLabel(data.current_clearing_type)}
            </Pill>
            {data.member_agencies.map((code) => (
              <Pill key={code} variant="member">
                {agencyLabel(code)} member
              </Pill>
            ))}
          </>
        }
        metaItems={[
          { label: "CRD", value: data.crd_number ?? "Pending" },
          { label: "SEC File", value: data.sec_file_number ?? "N/A" },
          { label: "CIK", value: data.cik ?? "N/A" },
          { value: location || "Location unknown" },
        ]}
      />

      <div className="flex flex-col gap-4 xl:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* ── Financials ── */}
          <SectionPanel eyebrow="Financials" title="Net capital and trend">
            <div className="mb-4 grid gap-3 md:grid-cols-3">
              <MiniStat
                label="Net Capital"
                value={<Copyable>{formatCurrency(data.latest_net_capital)}</Copyable>}
              />
              <MiniStat
                label="Excess Capital"
                value={
                  <Copyable>{formatCurrency(data.latest_excess_net_capital)}</Copyable>
                }
              />
              <MiniStat
                label="YoY Growth"
                value={<Copyable>{formatPercent(data.yoy_growth)}</Copyable>}
                valueClassName={
                  data.yoy_growth === null
                    ? "text-[var(--text-muted,#94a3b8)]"
                    : data.yoy_growth >= 0
                    ? "text-[#16a34a]"
                    : "text-[var(--pill-red-text,#b91c1c)]"
                }
                helper={
                  data.yoy_growth === null ? "Requires 2+ years of data" : undefined
                }
              />
              <MiniStat
                label="3-Yr CAGR"
                value={<Copyable>{formatPercent(data.three_year_cagr)}</Copyable>}
                valueClassName={
                  data.three_year_cagr === null
                    ? "text-[var(--text-muted,#94a3b8)]"
                    : data.three_year_cagr >= 0
                    ? "text-[#16a34a]"
                    : "text-[var(--pill-red-text,#b91c1c)]"
                }
                helper={
                  data.three_year_cagr === null
                    ? "Requires 3 years of financial history"
                    : "Compound annual growth over 3 years"
                }
              />
            </div>
            <FinancialTrendChart points={chartPoints} />
          </SectionPanel>

          {/* ── People ── */}
          <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
            {!hasPeople ? (
              <p className="text-sm text-[var(--text-muted,#94a3b8)]">
                No owners, officers, or contacts on file yet.
              </p>
            ) : null}

            {data.direct_owners && data.direct_owners.length > 0 ? (
              <PeopleTable
                title="Direct Owners"
                items={data.direct_owners}
                columns={[
                  {
                    header: "Name",
                    cell: (o) => (
                      <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                        {o.name ?? "—"}
                      </Copyable>
                    ),
                  },
                  { header: "Title", cell: (o) => o.title ?? "—" },
                  {
                    header: "Ownership",
                    cell: (o) => o.ownership_pct ?? "—",
                    className: "whitespace-nowrap",
                  },
                ]}
              />
            ) : null}

            {data.executive_officers && data.executive_officers.length > 0 ? (
              <PeopleTable
                title="Executive Officers"
                items={data.executive_officers.map((o) => ({
                  ...o,
                  contact: matchForFinra(o.name),
                }))}
                columns={[
                  {
                    header: "Name",
                    cell: (o) => (
                      <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                        {o.contact?.name ?? o.name ?? "—"}
                      </Copyable>
                    ),
                  },
                  { header: "Title", cell: (o) => o.title ?? "—" },
                  {
                    header: "Channels",
                    cell: (o) => <ChannelIconCell contact={o.contact} allowCopy />,
                    className: "whitespace-nowrap",
                  },
                ]}
              />
            ) : null}

            {additionalContacts.length > 0 ? (
              <PeopleTable
                title="Additional contacts"
                items={additionalContacts}
                columns={[
                  {
                    header: "Name",
                    cell: (c) => (
                      <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                        {c.name}
                      </Copyable>
                    ),
                  },
                  { header: "Title", cell: (c) => c.title ?? "—" },
                  {
                    header: "Channels",
                    cell: (c) => <ChannelIconCell contact={c} allowCopy />,
                    className: "whitespace-nowrap",
                  },
                ]}
              />
            ) : null}
          </SectionPanel>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* ── Assessment ── */}
          <SectionPanel eyebrow="Assessment" title="Firm profile overview">
            <div className="grid gap-3 md:grid-cols-2">
              <MiniStat
                label="Registration Status"
                value={
                  data.registration_compliance.registration_status ||
                  "Not available"
                }
                compact
              />
              <MiniStat
                label="Registration Date"
                value={formatDate(data.registration_compliance.registration_date)}
                compact
              />
              <MiniStat label="Address" value={location || "Not available"} compact />
              <MiniStat
                label="Branch Count"
                value={
                  data.registration_compliance.branch_count !== null
                    ? String(data.registration_compliance.branch_count)
                    : "Not available"
                }
                compact
              />
            </div>

            {/* Alternate Names — firm-filed DBAs / historical predecessor
                names. Hidden when the firm has no alternates so the card
                stays compact for single-name firms. */}
            {data.dba_names && data.dba_names.length > 0 ? (
              <div className="mt-4">
                <div className="flex items-center gap-2">
                  <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                    Alternate Names
                  </p>
                  <Pill variant="info">{data.dba_names.length} names</Pill>
                </div>
                <CollapsiblePillList items={data.dba_names} />
              </div>
            ) : null}

            {/* Types of Business */}
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                  Types of Business
                </p>
                {data.types_of_business_total ? (
                  <Pill variant="info">{data.types_of_business_total} types</Pill>
                ) : null}
              </div>
              {data.types_of_business && data.types_of_business.length > 0 ? (
                <CollapsiblePillList items={data.types_of_business} />
              ) : (
                <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">
                  Not available
                </p>
              )}
              {data.types_of_business_other ? (
                <div className="mt-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                    Other Business Activities
                  </p>
                  <p className="mt-1">{data.types_of_business_other}</p>
                </div>
              ) : null}
            </div>

            {data.firm_operations_text ? (
              <p className="mt-4 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
                {data.firm_operations_text}
              </p>
            ) : null}

            {/* Registration & compliance summary rows. */}
            <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm text-[var(--text-dim,#475569)]">
              <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Business Type
              </dt>
              <dd>{data.registration_compliance.business_type ?? "Not available"}</dd>
              <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                SEC File #
              </dt>
              <dd className="font-mono text-[var(--text,#0f172a)]">
                <Copyable>
                  {data.registration_compliance.sec_file_number ?? "N/A"}
                </Copyable>
              </dd>
              <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                CRD #
              </dt>
              <dd className="font-mono text-[var(--text,#0f172a)]">
                <Copyable>
                  {data.registration_compliance.crd_number ?? "Pending"}
                </Copyable>
              </dd>
            </dl>

            <div
              className={`mt-4 rounded-2xl px-4 py-4 text-sm ${
                data.deficiency_status.is_deficient
                  ? "bg-[rgba(239,68,68,0.08)] text-[var(--pill-red-text,#b91c1c)]"
                  : "bg-[rgba(16,185,129,0.08)] text-[var(--pill-green-text,#047857)]"
              }`}
            >
              <p className="font-semibold">
                {data.deficiency_status.is_deficient
                  ? "Deficiency notice active"
                  : "No active deficiency notice"}
              </p>
              <p className="mt-2 leading-6">{data.deficiency_status.message}</p>
            </div>
          </SectionPanel>

          {/* ── Relationship ── */}
          <SectionPanel eyebrow="Relationship" title="Clearing and introducing mapping">
            <div className="mb-4 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4 text-sm text-[var(--text-dim,#475569)]">
              <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                Clearing Arrangements
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Pill variant={clearingTypeVariant(data.current_clearing_type)}>
                  {clearingTypeLabel(data.current_clearing_type)}
                </Pill>
              </div>
              {data.current_clearing_partner ? (
                <p className="mt-2">
                  Clearing through:{" "}
                  <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                    {data.current_clearing_partner}
                  </Copyable>
                </p>
              ) : null}
            </div>

            {data.introducing_arrangements.length > 0 ? (
              <div className="mb-4">
                <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                  Introducing Arrangements
                </p>
                <div className="mt-2 space-y-2">
                  {data.introducing_arrangements.map((arr) => {
                    const parsed = parseArrangementBlob(
                      [arr.statement, arr.description].filter(Boolean).join(" "),
                    );
                    const name = arr.business_name || parsed.partnerName;
                    const effective = arr.effective_date
                      ? formatDate(arr.effective_date)
                      : parsed.effectiveDate;
                    return (
                      <div
                        key={arr.id}
                        className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                      >
                        {name ? (
                          <p className="font-semibold text-[var(--text,#0f172a)]">
                            {name}
                          </p>
                        ) : null}
                        <ArrangementFields
                          crd={parsed.partnerCrd}
                          address={parsed.partnerAddress}
                          effective={effective}
                          details={parsed.description || parsed.intro}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            {data.industry_arrangements.length > 0 ? (
              <div className="mb-4">
                <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                  Industry Arrangements
                </p>
                <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
                  Determines whether the firm is truly self-clearing or relies on a
                  third party.
                </p>
                <div className="mt-2 space-y-2">
                  {data.industry_arrangements.map((arr) => {
                    const kindLabel =
                      arr.kind === "books_records"
                        ? "Books / records"
                        : arr.kind === "accounts_funds"
                        ? "Accounts, funds, or securities"
                        : "Customer accounts, funds, or securities";
                    const parsed = arr.has_arrangement
                      ? parseArrangementBlob(arr.description)
                      : null;
                    return (
                      <div
                        key={arr.id}
                        className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
                            {kindLabel}
                          </p>
                          <Pill variant={arr.has_arrangement ? "warning" : "healthy"}>
                            {arr.has_arrangement
                              ? "Maintained by a third party"
                              : "Not maintained by a third party"}
                          </Pill>
                        </div>
                        {arr.has_arrangement && parsed ? (
                          <div className="mt-3 space-y-2 text-sm text-[var(--text-dim,#475569)]">
                            {arr.partner_name || parsed.partnerName ? (
                              <p>
                                <span className="text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                                  Partner
                                </span>{" "}
                                <span className="font-semibold text-[var(--text,#0f172a)]">
                                  {arr.partner_name || parsed.partnerName}
                                </span>
                                {arr.partner_crd || parsed.partnerCrd ? (
                                  <span className="ml-2 text-xs text-[var(--text-muted,#94a3b8)]">
                                    CRD #{arr.partner_crd || parsed.partnerCrd}
                                  </span>
                                ) : null}
                              </p>
                            ) : null}
                            <ArrangementFields
                              crd={
                                arr.partner_name || parsed.partnerName
                                  ? null
                                  : arr.partner_crd || parsed.partnerCrd
                              }
                              address={arr.partner_address || parsed.partnerAddress}
                              effective={
                                arr.effective_date
                                  ? formatDate(arr.effective_date)
                                  : parsed.effectiveDate
                              }
                              details={parsed.description || parsed.intro}
                            />
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              Clearing History
            </p>
            <div className="mt-2 space-y-2">
              {data.clearing_arrangements.length === 0 ? (
                <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                  No clearing history available yet.
                </div>
              ) : (
                data.clearing_arrangements.map((item) => (
                  <div
                    key={item.id}
                    className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
                          {item.clearing_partner ?? "Partner not on file"}
                        </p>
                        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
                          Year {item.filing_year}
                        </p>
                      </div>
                      <Pill variant={clearingTypeVariant(item.clearing_type)}>
                        {clearingTypeLabel(item.clearing_type)}
                      </Pill>
                    </div>
                    <ArrangementFields
                      crd={null}
                      address={null}
                      effective={
                        item.agreement_date ? formatDate(item.agreement_date) : null
                      }
                      details={null}
                    />
                  </div>
                ))
              )}
            </div>
          </SectionPanel>
        </div>
      </div>

      <div className="mt-4">
        <DiscoveredEmailsSection rows={discoveredRows} />
      </div>
    </div>
  );
}
