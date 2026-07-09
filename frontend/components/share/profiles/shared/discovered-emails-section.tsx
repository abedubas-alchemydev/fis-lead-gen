"use client";

// Standalone, full-width "Discovered emails" section for the public DOX Share
// profiles. Email-extractor discoveries are pulled OUT of the (half-width)
// People panel and given their own card that spans the full profile width: a
// firm can accumulate 100+ discovered addresses, which — nested in the People
// panel's narrow column — crowd the owners/officers roster and squeeze the
// long addresses into a cramped table. Shared by the advisor, bank, and
// broker-dealer share profiles so the three surfaces stay in lock-step.

import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { PeopleTable } from "@/components/master-list/detail/people-table";
import { SectionPanel } from "@/components/ui/section-panel";
import { Copyable } from "@/components/share/profiles/shared/copyable";

// One email-extractor discovery projected onto the row shape PeopleTable +
// ChannelIconCell consume. Structurally identical to the per-profile
// DiscoveredRow shapes, which are assignable to this type.
export type DiscoveredEmailRow = {
  id: number;
  name: string;
  title: string | null;
  email: string;
  phone: string | null;
  linkedin_url: string | null;
};

export function DiscoveredEmailsSection({
  rows,
}: {
  rows: readonly DiscoveredEmailRow[];
}) {
  // Render nothing when the firm has no discoveries — the section only appears
  // when it has content, mirroring the other conditionally-rendered panels.
  if (rows.length === 0) return null;

  return (
    <SectionPanel
      eyebrow="Email extractor"
      title={`Discovered emails (${rows.length})`}
    >
      {/* SectionPanel already supplies the heading + count, so the table
          suppresses its own label and contributes only the pager. */}
      <PeopleTable
        title="Discovered emails"
        showTitle={false}
        scrollX
        items={rows}
        columns={[
          {
            header: "Email",
            cell: (c) => (
              <Copyable className="font-semibold text-[var(--text,#0f172a)]">
                {c.email}
              </Copyable>
            ),
          },
          {
            // Many discoveries carry no parsed person name (the row's name
            // falls back to the email upstream) — only show a name when it is
            // genuinely distinct from the address.
            header: "Name",
            cell: (c) => (c.name && c.name !== c.email ? c.name : "—"),
          },
          { header: "Title", cell: (c) => c.title ?? "—" },
          {
            header: "Channels",
            cell: (c) => <ChannelIconCell contact={c} allowCopy />,
            className: "whitespace-nowrap",
          },
        ]}
      />
    </SectionPanel>
  );
}
