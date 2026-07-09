"use client";

// Read-only bank profile for the public DOX Share surface. Pure
// presentational: renders entirely from the trimmed BankSharePayload prop —
// no fetching, no navigation, no auth, no enrichment controls, and no
// outbound document links (FDIC/OCC source pages and application PDFs
// surface as plain-text provenance only). Section layout mirrors the authed
// /banks/{id} detail page (bank-detail-client.tsx) minus everything
// interactive.

import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { PeopleTable } from "@/components/master-list/detail/people-table";
import { SectionPanel } from "@/components/ui/section-panel";
import { Pill, type PillVariant } from "@/components/ui/pill";
import {
  BankStatusPill,
  charterAuthorityLabel,
  NO_OCC_TIMELINE_EXPLANATION,
} from "@/components/banks/bank-status-pill";
import { formatCurrency, formatDate } from "@/lib/format";
import type { BankApplicationEventItem } from "@/lib/types";

import { Copyable } from "@/components/share/profiles/shared/copyable";
import { DiscoveredEmailsSection } from "@/components/share/profiles/shared/discovered-emails-section";
import { MiniStat } from "@/components/share/profiles/shared/mini-stat";
import { ProfileHeader } from "@/components/share/profiles/shared/profile-header";
import type {
  BankShareProfileProps,
  PublicContactItem,
} from "@/components/share/profiles/types";

// FDIC reports ASSET/DEP in $ thousands — scale to dollars for the shared
// compact-currency formatter so "$52,942 (thousands)" renders as "$52.9M".
// Mirrors formatFdicThousands in bank-detail-client.tsx.
function formatFdicThousands(value: number | null): string {
  return formatCurrency(value === null ? null : value * 1000);
}

// Timeline renders oldest-first (Receipt → Approved → Consummated) even
// though the BE relationship orders newest-first. NULL action dates sink to
// the bottom so an undated action never claims the "first step" slot.
// Mirrors chronological() in bank-detail-client.tsx.
function chronological(
  events: BankApplicationEventItem[],
): BankApplicationEventItem[] {
  return [...events].sort((a, b) => {
    const aDate = a.action_date ?? "9999-12-31";
    const bDate = b.action_date ?? "9999-12-31";
    if (aDate !== bDate) return aDate < bDate ? -1 : 1;
    return a.id - b.id;
  });
}

// contacts.role_context → friendly chip label + tone. Vocabulary mirrors
// the BE extractor (contact_person | organizer | proposed_officer |
// counsel); unknown/future values surface capitalized in the neutral tone
// rather than being hidden. Per-surface copy of the maps in
// bank-detail-client.tsx.
const CONTACT_ROLE_LABELS: Record<string, string> = {
  contact_person: "Contact person",
  organizer: "Organizer",
  proposed_officer: "Proposed officer",
  counsel: "Counsel",
};

const CONTACT_ROLE_VARIANTS: Record<string, PillVariant> = {
  contact_person: "info",
  organizer: "self",
  proposed_officer: "member",
  counsel: "noncarry",
};

function contactRoleLabel(roleContext: string): string {
  return (
    CONTACT_ROLE_LABELS[roleContext] ??
    (roleContext.charAt(0).toUpperCase() + roleContext.slice(1)).replace(
      /_/g,
      " ",
    )
  );
}

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

export function BankShareProfile({ data }: BankShareProfileProps) {
  const location = [data.city, data.state].filter(Boolean).join(", ");
  const addressLine = [data.address, data.city, data.state, data.zip]
    .filter(Boolean)
    .join(", ");
  const events = chronological(data.application_events);

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

  return (
    <div className="min-w-0 animate-fade-in">
      <ProfileHeader
        name={data.name}
        website={data.website}
        pills={
          <>
            <BankStatusPill status={data.charter_status} />
            {data.charter_authority ? (
              <Pill variant="info">
                {charterAuthorityLabel(data.charter_authority)} charter
              </Pill>
            ) : null}
            {data.digital_assets ? (
              <Pill variant="self">Digital Assets</Pill>
            ) : null}
          </>
        }
        metaItems={[
          ...(data.fdic_cert
            ? [{ label: "FDIC CERT", value: data.fdic_cert }]
            : []),
          ...(data.fed_rssd
            ? [{ label: "FED RSSD", value: data.fed_rssd }]
            : []),
          ...(data.occ_charter_number
            ? [{ label: "OCC Charter", value: data.occ_charter_number }]
            : []),
          ...(data.occ_control_number
            ? [{ label: "OCC Control", value: data.occ_control_number }]
            : []),
          ...(location ? [{ value: location }] : []),
        ]}
      />

      {/* ── KPI strip ── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Total assets"
          value={<Copyable>{formatFdicThousands(data.asset)}</Copyable>}
          helper={data.asset !== null ? "FDIC BankFind, as reported" : undefined}
        />
        <MiniStat
          label="Total deposits"
          value={<Copyable>{formatFdicThousands(data.deposits)}</Copyable>}
          helper={
            data.deposits !== null ? "FDIC BankFind, as reported" : undefined
          }
        />
        <MiniStat
          label="Offices"
          value={<Copyable>{data.offices?.toLocaleString() ?? "—"}</Copyable>}
        />
        <MiniStat
          label="Established"
          value={
            <Copyable>
              {data.established_date ? formatDate(data.established_date) : "—"}
            </Copyable>
          }
          helper={
            data.established_date === null
              ? "Not yet established — application in progress"
              : undefined
          }
        />
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* ── Institution profile ── */}
          <SectionPanel eyebrow="Profile" title="Institution profile">
            <div className="grid gap-3 md:grid-cols-2">
              <MiniStat
                label="Charter authority"
                value={charterAuthorityLabel(data.charter_authority)}
                compact
              />
              <MiniStat label="Bank class" value={data.bkclass ?? "—"} compact />
              <MiniStat
                label="Primary regulator"
                value={data.regulator ?? "—"}
                compact
              />
              <MiniStat
                label="FDIC insured since"
                value={data.insured_date ? formatDate(data.insured_date) : "—"}
                compact
              />
              <MiniStat
                label="Application received"
                value={
                  data.application_received_date
                    ? formatDate(data.application_received_date)
                    : "—"
                }
                compact
              />
              <MiniStat
                label="Last action"
                value={
                  data.last_action_date ? formatDate(data.last_action_date) : "—"
                }
                compact
              />
              <MiniStat
                label="Charter type"
                value={data.charter_type ?? "—"}
                compact
              />
              <MiniStat
                label="LEI"
                value={<Copyable>{data.lei ?? "—"}</Copyable>}
                compact
              />
            </div>

            <div className="mt-4">
              <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
                Address
              </p>
              <p className="mt-1 text-sm text-[var(--text-dim,#475569)]">
                {addressLine || "Not on file yet."}
              </p>
            </div>

            {/* Provenance footer — official sources as plain text. The share
                surface deliberately renders no outbound FDIC/OCC links or
                application-PDF links. */}
            {data.source_links.length > 0 ? (
              <div className="mt-4 border-t border-dashed border-[var(--border,rgba(30,64,175,0.1))] pt-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  Official sources
                </p>
                <ul className="mt-1.5 space-y-1 text-[11px] text-[var(--text-muted,#94a3b8)]">
                  {data.source_links.map((link) => (
                    <li key={link.url} className="min-w-0">
                      <span className="font-medium text-[var(--text-dim,#475569)]">
                        {link.label}
                      </span>
                      <span className="ml-2 break-all">{link.url}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </SectionPanel>

          {/* ── People ── */}
          <SectionPanel eyebrow="People" title="Contacts">
            {contactRows.length === 0 ? (
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                No contacts on file yet.
              </div>
            ) : null}

            {contactRows.length > 0 ? (
              <PeopleTable
                title="Contacts"
                items={contactRows}
                columns={[
                  {
                    header: "Name",
                    cell: (c) => (
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                          {c.name}
                        </Copyable>
                        {c.role_context ? (
                          <Pill
                            variant={
                              CONTACT_ROLE_VARIANTS[c.role_context] ?? "unknown"
                            }
                          >
                            {contactRoleLabel(c.role_context)}
                          </Pill>
                        ) : null}
                      </div>
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
          {/* ── Application timeline — OCC charter-application actions
              (Receipt → Approved → Consummated), oldest first, no filing
              links on the share surface. ── */}
          <SectionPanel eyebrow="Charter Application" title="Application timeline">
            {events.length === 0 ? (
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                {data.charter_authority === "STATE"
                  ? `${NO_OCC_TIMELINE_EXPLANATION}. This institution arrives via FDIC BankFind only.`
                  : "No OCC application events on file yet."}
              </div>
            ) : (
              <ol className="relative ml-2 space-y-5 border-l border-[var(--border-2,rgba(30,64,175,0.16))] pl-5">
                {events.map((event, idx) => {
                  const isLatest = idx === events.length - 1;
                  return (
                    <li key={event.id} className="relative">
                      {/* Timeline dot — latest action gets the accent fill. */}
                      <span
                        aria-hidden
                        className={`absolute -left-[26.5px] top-1 h-3 w-3 rounded-full border-2 ${
                          isLatest
                            ? "border-[var(--accent,#6366f1)] bg-[var(--accent,#6366f1)]"
                            : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)]"
                        }`}
                      />
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                        <p className="text-[13.5px] font-semibold text-[var(--text,#0f172a)]">
                          {event.action}
                        </p>
                        <p className="text-[12px] tabular-nums text-[var(--text-muted,#94a3b8)]">
                          {event.action_date
                            ? formatDate(event.action_date)
                            : "Date not published"}
                        </p>
                      </div>
                      {event.filing_type ? (
                        <div className="mt-0.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
                          <span>{event.filing_type}</span>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            )}
          </SectionPanel>
        </div>
      </div>

      <div className="mt-4">
        <DiscoveredEmailsSection rows={discoveredRows} />
      </div>
    </div>
  );
}
