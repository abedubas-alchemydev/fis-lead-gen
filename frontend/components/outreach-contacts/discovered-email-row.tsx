"use client";

import { ExternalLink, Mail, MailPlus, Phone, ScanLine } from "lucide-react";
import { useState } from "react";

import { OutreachModal } from "@/components/master-list/outreach-modal";
import { Pill, type PillVariant } from "@/components/ui/pill";
import type { DiscoveredContactRow } from "@/lib/outreach-contacts";

// Discovered-email rows come from the Email Extractor (discovered_email), not
// the typed contact tables, so they have no firm/contact FK. The "Outreach"
// button therefore reuses the ad-hoc compose path -- exactly like the Email
// Extractor scan page's OutreachCell -- addressing the discovered address as
// the recipient (enrichment just lets the AI draft open with a real name).

// enrichment_status -> Pill variant + label. Mirrors the SMTP-status mapping
// on the scan detail page (healthy/critical/unknown), so the pill reads the
// same across both surfaces. Anything unrecognized falls back to a neutral
// "unknown" pill carrying the raw status string.
const ENRICHMENT_STATUS_STYLES: Record<
  string,
  { variant: PillVariant; label: string }
> = {
  enriched: { variant: "healthy", label: "Enriched" },
  not_enriched: { variant: "unknown", label: "Not enriched" },
  no_match: { variant: "unknown", label: "Not found" },
  error: { variant: "critical", label: "Enrich error" },
};

function EnrichmentStatusPill({
  status,
}: {
  status: string;
}): React.ReactElement {
  const cfg = ENRICHMENT_STATUS_STYLES[status];
  if (cfg) {
    return <Pill variant={cfg.variant}>{cfg.label}</Pill>;
  }
  return <Pill variant="unknown">{status}</Pill>;
}

interface DiscoveredEmailRowProps {
  row: DiscoveredContactRow;
}

export function DiscoveredEmailRow({
  row,
}: DiscoveredEmailRowProps): React.ReactElement {
  const [outreachOpen, setOutreachOpen] = useState(false);

  return (
    <li className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        {row.enriched_name ? (
          <span className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
            {row.enriched_name}
          </span>
        ) : null}
        {row.enriched_title ? (
          <span className="text-[12px] text-[var(--text-dim,#475569)]">
            {row.enriched_title}
          </span>
        ) : null}
        <EnrichmentStatusPill status={row.enrichment_status} />
        <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-[2px] text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
          <ScanLine className="h-3 w-3" strokeWidth={2} aria-hidden />
          from Email Extractor
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--text-dim,#475569)]">
        <span className="inline-flex items-center gap-1">
          <Mail className="h-3 w-3" strokeWidth={2} />
          <button
            type="button"
            onClick={() => setOutreachOpen(true)}
            title="Compose outreach"
            className="cursor-pointer hover:text-[#6366f1]"
          >
            {row.email}
          </button>
        </span>
        {row.enriched_phone ? (
          <span className="inline-flex items-center gap-1">
            <Phone className="h-3 w-3" strokeWidth={2} />
            <a
              href={`tel:${row.enriched_phone}`}
              className="hover:text-[#6366f1]"
            >
              {row.enriched_phone}
            </a>
          </span>
        ) : null}
        {row.enriched_linkedin_url ? (
          <a
            href={row.enriched_linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 hover:text-[#6366f1]"
          >
            <ExternalLink className="h-3 w-3" strokeWidth={2} />
            LinkedIn
          </a>
        ) : null}
        <button
          type="button"
          onClick={() => setOutreachOpen(true)}
          className="ml-auto inline-flex items-center gap-1 rounded-md bg-[var(--accent,#6366f1)] px-2 py-0.5 text-[11px] font-semibold text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
        >
          <MailPlus className="h-3 w-3" strokeWidth={2} aria-hidden />
          Outreach
        </button>
      </div>

      {outreachOpen ? (
        <OutreachModal
          entityKind="adhoc"
          entityId={0}
          entityName=""
          contact={{
            id: row.id,
            name: row.enriched_name ?? "",
            title: row.enriched_title ?? "",
            email: row.email,
          }}
          onClose={() => setOutreachOpen(false)}
        />
      ) : null}
    </li>
  );
}
