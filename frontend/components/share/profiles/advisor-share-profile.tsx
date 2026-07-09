"use client";

// Read-only investment-advisor profile for the public DOX Share surface.
// Pure presentational: renders entirely from the trimmed AdvisorSharePayload
// prop — no fetching, no navigation, no auth, no enrichment controls, no
// favorites, and no filings list (the payload carries `filings` but the
// share surface doesn't render that section). Section layout mirrors the
// authed /advisor-list/{id} detail page (advisor-detail-client.tsx) minus
// everything interactive.

import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { PeopleTable } from "@/components/master-list/detail/people-table";
import { SectionPanel } from "@/components/ui/section-panel";
import { Pill } from "@/components/ui/pill";
import { CollapsiblePillList } from "@/components/ui/collapsible-pill-list";
import { agencyLabel } from "@/components/master-list/detail/clearing-membership-helpers";
import { formatCurrency, formatDate } from "@/lib/format";

import { Copyable } from "@/components/share/profiles/shared/copyable";
import { DiscoveredEmailsSection } from "@/components/share/profiles/shared/discovered-emails-section";
import { MiniStat } from "@/components/share/profiles/shared/mini-stat";
import { ProfileHeader } from "@/components/share/profiles/shared/profile-header";
import type {
  AdvisorShareProfileProps,
  PublicContactItem,
} from "@/components/share/profiles/types";

// The share payload deliberately omits DB ids, but ChannelIconCell's contact
// prop structurally requires an `id` (never read at runtime) — synthesize
// one from the row index.
type ContactRow = PublicContactItem & { id: number };

type DiscoveredRow = {
  id: number;
  name: string;
  title: string | null;
  email: string;
  phone: string | null;
  linkedin_url: string | null;
};

export function AdvisorShareProfile({ data }: AdvisorShareProfileProps) {
  const location = [data.city, data.state].filter(Boolean).join(", ");
  const directOwners = data.direct_owners ?? [];
  const indirectOwners = data.indirect_owners ?? [];
  const executiveOfficers = data.executive_officers ?? [];
  const advisoryActivities = data.advisory_activities ?? [];
  const clientTypes = data.client_types ?? [];
  const clientCounts = data.client_counts ?? null;

  const contactRows: ContactRow[] = data.contacts.map((contact, index) => ({
    ...contact,
    id: index,
  }));
  const discoveredRows: DiscoveredRow[] = data.discovered_emails.map(
    (row, index) => ({
      id: index,
      name: row.name ?? row.email,
      title: row.title,
      email: row.email,
      phone: row.phone,
      linkedin_url: row.linkedin_url,
    }),
  );

  // Discovered emails render in their own full-width section below the grid,
  // so they no longer count toward the People panel's empty state.
  const hasPeople =
    directOwners.length > 0 ||
    executiveOfficers.length > 0 ||
    indirectOwners.length > 0 ||
    contactRows.length > 0;

  return (
    <div className="min-w-0 animate-fade-in">
      <ProfileHeader
        name={data.name}
        website={data.website}
        pills={
          <>
            {data.files_13f ? <Pill variant="healthy">13F filer</Pill> : null}
            {data.member_agencies.map((code) => (
              <Pill key={code} variant="member">
                {agencyLabel(code)} member
              </Pill>
            ))}
          </>
        }
        subtitle={
          data.legal_name && data.legal_name !== data.name ? (
            <p className="mt-1 text-[13px] text-[var(--text-dim,#475569)]">
              Legal name:{" "}
              <Copyable className="font-medium text-[var(--text,#0f172a)]">
                {data.legal_name}
              </Copyable>
            </p>
          ) : null
        }
        metaItems={[
          ...(data.crd_number ? [{ label: "CRD", value: data.crd_number }] : []),
          ...(data.cik ? [{ label: "CIK", value: data.cik }] : []),
          ...(data.sec_file_number
            ? [{ label: "SEC File", value: data.sec_file_number }]
            : []),
          ...(location ? [{ value: location }] : []),
        ]}
      />

      {/* ── KPI strip ── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Regulatory AUM"
          value={<Copyable>{formatCurrency(data.regulatory_aum)}</Copyable>}
        />
        <MiniStat
          label="Total clients"
          value={<Copyable>{data.total_clients?.toLocaleString() ?? "—"}</Copyable>}
        />
        <MiniStat
          label="Last filing"
          value={<Copyable>{formatDate(data.last_filing_date)}</Copyable>}
        />
        <MiniStat
          label="Latest 13F"
          value={<Copyable>{formatDate(data.latest_13f_filing_date)}</Copyable>}
        />
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        {/* ── Firm overview ── */}
        <SectionPanel eyebrow="Overview" title="Registration & activities">
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat label="Status" value={data.status || "—"} compact />
            <MiniStat
              label="Registration date"
              value={formatDate(data.registration_date)}
              compact
            />
          </div>

          <div className="mt-4">
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              Advisory activities
            </p>
            {advisoryActivities.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {advisoryActivities.map((activity) => (
                  <Pill key={activity} variant="info">
                    {activity}
                  </Pill>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">
                Not extracted yet.
              </p>
            )}
          </div>

          <div className="mt-4">
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              Client types
            </p>
            {clientTypes.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {clientTypes.map((type) => (
                  <span
                    key={type}
                    className="rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1 text-[11px] text-[var(--text-dim,#475569)]"
                  >
                    {type}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-[var(--text-muted,#94a3b8)]">
                Not extracted yet.
              </p>
            )}
          </div>

          {clientCounts && Object.keys(clientCounts).length > 0 ? (
            <div className="mt-4 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Client counts (Item 5.D.3)
              </p>
              <dl className="mt-2 grid gap-x-4 gap-y-1 text-[13px] text-[var(--text-dim,#475569)] sm:grid-cols-2">
                {Object.entries(clientCounts).map(([key, count]) => (
                  <div
                    key={key}
                    className="flex items-baseline justify-between gap-2"
                  >
                    <dt className="capitalize">{key.replace(/_/g, " ")}</dt>
                    <dd className="font-mono tabular-nums text-[var(--text,#0f172a)]">
                      {count.toLocaleString()}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          {data.firm_operations_text ? (
            <p className="mt-4 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
              {data.firm_operations_text}
            </p>
          ) : null}

          {/* Alternative Names — Form ADV Schedule D §1.B "Other Business
              Names". Hidden when the firm has none so the card stays
              compact. */}
          {data.other_business_names && data.other_business_names.length > 0 ? (
            <div className="mt-4">
              <div className="flex items-center gap-2">
                <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                  Alternative Names
                </p>
                <Pill variant="info">
                  {data.other_business_names.length} names
                </Pill>
              </div>
              <CollapsiblePillList items={data.other_business_names} />
            </div>
          ) : null}
        </SectionPanel>

        {/* ── Form ADV financials ── */}
        <SectionPanel
          eyebrow="Form ADV"
          title="Regulatory assets under management"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Discretionary AUM"
              value={<Copyable>{formatCurrency(data.discretionary_aum)}</Copyable>}
              compact
            />
            <MiniStat
              label="Non-discretionary AUM"
              value={
                <Copyable>{formatCurrency(data.non_discretionary_aum)}</Copyable>
              }
              compact
            />
          </div>
        </SectionPanel>

        {/* ── People ── */}
        <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
          {!hasPeople ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No owners, officers, or contacts on file yet.
            </p>
          ) : null}

          {directOwners.length > 0 ? (
            <PeopleTable
              title="Direct owners"
              items={directOwners}
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

          {/* Executive officers — mirror the authed page's merged table:
              when enriched contacts exist, drive from them (title-cased
              names + Channels); pre-enrichment, fall back to the raw Form
              ADV officer list so the roster still shows. */}
          {contactRows.length > 0 ? (
            <PeopleTable
              title="Executive officers"
              items={contactRows}
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
          ) : executiveOfficers.length > 0 ? (
            <PeopleTable
              title="Executive officers"
              items={executiveOfficers}
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
              ]}
            />
          ) : null}

          {indirectOwners.length > 0 ? (
            <PeopleTable
              title="Indirect owners"
              items={indirectOwners}
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

        </SectionPanel>
      </div>

      <div className="mt-4">
        <DiscoveredEmailsSection rows={discoveredRows} />
      </div>
    </div>
  );
}
