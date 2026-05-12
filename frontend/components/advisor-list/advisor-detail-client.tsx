"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useSearchParams } from "next/navigation";

import { ArrowLeft, ExternalLink } from "lucide-react";

import { apiRequest } from "@/lib/api";
import {
  buildAdvisorListUrl,
  parseReturnParam,
  ADVISOR_LIST_STATE_DEFAULTS,
} from "@/lib/advisor-list-state";
import { formatCurrency, formatDate } from "@/lib/format";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import type { InvestmentAdvisorProfileResponse } from "@/lib/types";

// Detail view for /advisor-list/{id}. Mirrors the design system used on
// /master-list/{id}: page topbar with breadcrumbs + h1 + meta line, KPI
// strip of MiniStat tiles, and SectionPanel cards on a 2-column grid.
export function AdvisorDetailClient({ advisorId }: { advisorId: string }) {
  const searchParams = useSearchParams();
  const [data, setData] = useState<InvestmentAdvisorProfileResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiRequest<InvestmentAdvisorProfileResponse>(
      `/api/v1/investment-advisors/${advisorId}/profile`,
    )
      .then(setData)
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load advisor");
      });
  }, [advisorId]);

  // Restore the user's filter/sort state on back-nav, falling back to
  // the bare list URL if no return envelope was passed.
  const returnState = parseReturnParam(searchParams.get("return"));
  const backHref = (
    returnState
      ? buildAdvisorListUrl(returnState)
      : buildAdvisorListUrl(ADVISOR_LIST_STATE_DEFAULTS)
  ) as Route;

  if (error) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          Couldn&apos;t load advisor profile: {error}
        </div>
      </div>
    );
  }

  if (data === null) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div
          className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8"
          style={{
            boxShadow:
              "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
          }}
        >
          <div className="h-6 w-56 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-4 h-4 w-full animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
          <div className="mt-8 grid gap-4 xl:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]"
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  const { advisor, contacts, filings } = data;
  const location = [advisor.city, advisor.state].filter(Boolean).join(", ");
  const directOwners = advisor.direct_owners ?? [];
  const indirectOwners = advisor.indirect_owners ?? [];
  const executiveOfficers = advisor.executive_officers ?? [];
  const advisoryActivities = advisor.advisory_activities ?? [];
  const clientTypes = advisor.client_types ?? [];
  const clientCounts = advisor.client_counts ?? null;

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* ── Topbar: breadcrumbs + h1 + meta ─────────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={backHref}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Investment Advisors
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Firm
            Detail
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            {advisor.name}
          </h1>
          {advisor.legal_name && advisor.legal_name !== advisor.name ? (
            <p className="mt-1 text-[13px] text-[var(--text-dim,#475569)]">
              Legal name:{" "}
              <span className="font-medium text-[var(--text,#0f172a)]">
                {advisor.legal_name}
              </span>
            </p>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {advisor.crd_number ? (
              <span>
                CRD{" "}
                <span className="font-mono text-[var(--text-dim,#475569)]">
                  {advisor.crd_number}
                </span>
              </span>
            ) : null}
            {advisor.cik ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  CIK{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {advisor.cik}
                  </span>
                </span>
              </>
            ) : null}
            {advisor.sec_file_number ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  SEC File{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {advisor.sec_file_number}
                  </span>
                </span>
              </>
            ) : null}
            {location ? (
              <>
                <span aria-hidden>·</span>
                <span>{location}</span>
              </>
            ) : null}
          </div>
        </div>
      </div>

      {/* ── Status pills row ────────────────────────────────────────────── */}
      {advisor.files_13f ? (
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Pill variant="healthy">13F filer</Pill>
        </div>
      ) : null}

      {/* ── Back-nav row ────────────────────────────────────────────────── */}
      <div className="mb-5">
        <Link
          href={backHref}
          className="inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
          Back to advisors
        </Link>
      </div>

      {/* ── KPI strip ───────────────────────────────────────────────────── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Regulatory AUM"
          value={formatCurrency(advisor.regulatory_aum)}
        />
        <MiniStat
          label="Total clients"
          value={advisor.total_clients?.toLocaleString() ?? "—"}
        />
        <MiniStat
          label="Last filing"
          value={formatDate(advisor.last_filing_date)}
        />
        <MiniStat
          label="Latest 13F"
          value={formatDate(advisor.latest_13f_filing_date)}
        />
      </div>

      {/* ── 2-column section grid ───────────────────────────────────────── */}
      <div className="grid gap-4 xl:grid-cols-2">
        {/* Form ADV details */}
        <SectionPanel
          eyebrow="Form ADV"
          title="Advisory activities & client mix"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Discretionary AUM"
              value={formatCurrency(advisor.discretionary_aum)}
              compact
            />
            <MiniStat
              label="Non-discretionary AUM"
              value={formatCurrency(advisor.non_discretionary_aum)}
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

          {advisor.firm_operations_text ? (
            <p className="mt-4 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
              {advisor.firm_operations_text}
            </p>
          ) : null}
        </SectionPanel>

        {/* Firm overview */}
        <SectionPanel eyebrow="Overview" title="Registration & web presence">
          <div className="grid gap-3 md:grid-cols-2">
            <MiniStat
              label="Status"
              value={advisor.status || "—"}
              compact
            />
            <MiniStat
              label="Registration date"
              value={formatDate(advisor.registration_date)}
              compact
            />
            <MiniStat
              label="Formation date"
              value={formatDate(advisor.formation_date)}
              compact
            />
            <MiniStat
              label="Source"
              value={advisor.matched_source || "—"}
              compact
            />
          </div>

          {advisor.website ? (
            <div className="mt-4 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                Website
              </p>
              <a
                href={advisor.website}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 text-[13px] font-medium text-[var(--accent,#6366f1)] hover:underline"
              >
                {advisor.website}
                <ExternalLink className="h-3 w-3" strokeWidth={2} />
              </a>
              {advisor.website_source ? (
                <p className="mt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                  Source: {advisor.website_source}
                </p>
              ) : null}
            </div>
          ) : (
            <p className="mt-4 text-sm text-[var(--text-muted,#94a3b8)]">
              No website on file.
            </p>
          )}

          {advisor.filings_index_url ? (
            <a
              href={advisor.filings_index_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-4 inline-flex items-center gap-1.5 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
            >
              <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
              EDGAR filings index
            </a>
          ) : null}
        </SectionPanel>

        {/* People */}
        <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
          {directOwners.length === 0 &&
          executiveOfficers.length === 0 &&
          indirectOwners.length === 0 &&
          contacts.length === 0 ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No owners, officers, or contacts on file yet.
            </p>
          ) : null}

          {directOwners.length > 0 ? (
            <PeopleSubGroup title="Direct owners">
              {directOwners.map((owner, i) => (
                <PersonCard
                  key={`direct-${i}`}
                  name={owner.name ?? "—"}
                  title={owner.title ?? null}
                  extra={
                    owner.ownership_pct
                      ? `Ownership: ${owner.ownership_pct}`
                      : null
                  }
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {executiveOfficers.length > 0 ? (
            <PeopleSubGroup title="Executive officers">
              {executiveOfficers.map((officer, i) => (
                <PersonCard
                  key={`officer-${i}`}
                  name={officer.name ?? "—"}
                  title={officer.title ?? null}
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {indirectOwners.length > 0 ? (
            <PeopleSubGroup title="Indirect owners">
              {indirectOwners.map((owner, i) => (
                <PersonCard
                  key={`indirect-${i}`}
                  name={owner.name ?? "—"}
                  title={owner.title ?? null}
                  extra={
                    owner.ownership_pct
                      ? `Ownership: ${owner.ownership_pct}`
                      : null
                  }
                />
              ))}
            </PeopleSubGroup>
          ) : null}

          {contacts.length > 0 ? (
            <PeopleSubGroup title="Enriched contacts">
              {contacts.map((contact) => (
                <PersonCard
                  key={`contact-${contact.id}`}
                  name={contact.name}
                  title={contact.title}
                  email={contact.email}
                  source={`${contact.source} · ${formatDate(contact.enriched_at)}`}
                />
              ))}
            </PeopleSubGroup>
          ) : null}
        </SectionPanel>

        {/* Filings */}
        <SectionPanel eyebrow="Filings" title="Recent regulatory filings">
          {filings.length === 0 ? (
            <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
              No filings tracked yet.
            </div>
          ) : (
            <div className="space-y-2">
              {filings.map((filing) => (
                <div
                  key={filing.id}
                  className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-[var(--text,#0f172a)]">
                        {filing.form_type}
                      </p>
                      {filing.summary ? (
                        <p className="mt-1 text-sm text-[var(--text-dim,#475569)]">
                          {filing.summary}
                        </p>
                      ) : null}
                    </div>
                    {filing.priority ? (
                      <Pill variant="info">{filing.priority}</Pill>
                    ) : null}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-4 text-[12px] text-[var(--text-muted,#94a3b8)]">
                    <span>{formatDate(filing.filed_at)}</span>
                    {filing.source_filing_url ? (
                      <a
                        href={filing.source_filing_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-[var(--accent,#6366f1)] hover:underline"
                      >
                        Open filing
                        <ExternalLink className="h-3 w-3" strokeWidth={2} />
                      </a>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </SectionPanel>
      </div>
    </div>
  );
}

// Inline mini-stat card. Mirrors the MiniStat helper inside
// broker-dealer-detail-client.tsx so KPI tiles look identical across the
// two detail pages (surface-2 background, eyebrow label, semibold value).
function MiniStat({
  label,
  value,
  helper,
  compact,
}: {
  label: string;
  value: React.ReactNode;
  helper?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 ${
        compact ? "py-3" : "py-4"
      } text-sm`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p
        className={`mt-1 ${
          compact
            ? "text-[13px] text-[var(--text,#0f172a)]"
            : "text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]"
        }`}
      >
        {value}
      </p>
      {helper ? (
        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
          {helper}
        </p>
      ) : null}
    </div>
  );
}

function PeopleSubGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4 last:mb-0">
      <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
        {title}
      </p>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function PersonCard({
  name,
  title,
  email,
  extra,
  source,
}: {
  name: string;
  title?: string | null;
  email?: string | null;
  extra?: string | null;
  source?: string;
}) {
  return (
    <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]">
      <p className="font-semibold text-[var(--text,#0f172a)]">{name}</p>
      {title ? <p className="mt-1">{title}</p> : null}
      {email ? (
        <p className="mt-1">
          <a
            href={`mailto:${email}`}
            className="text-[var(--accent,#6366f1)] hover:underline"
          >
            {email}
          </a>
        </p>
      ) : null}
      {extra ? (
        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
          {extra}
        </p>
      ) : null}
      {source ? (
        <p className="mt-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
          {source}
        </p>
      ) : null}
    </div>
  );
}
