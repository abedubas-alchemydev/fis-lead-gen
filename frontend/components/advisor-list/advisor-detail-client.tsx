"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useSearchParams } from "next/navigation";

import { ArrowLeft } from "lucide-react";

import { apiRequest } from "@/lib/api";
import {
  buildAdvisorListUrl,
  parseReturnParam,
  ADVISOR_LIST_STATE_DEFAULTS,
} from "@/lib/advisor-list-state";
import { formatCurrency, formatDate } from "@/lib/format";
import { Pill } from "@/components/ui/pill";
import { PageSpinner } from "@/components/ui/spinner";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import type { InvestmentAdvisorProfileResponse } from "@/lib/types";

// PR 1 placeholder. Renders the firm header + a few key Form ADV fields
// from ``investment_advisors`` plus contact/filing list scaffolding.
// PR 3 fills in advisory-services pills, client mix, and Schedule A/B
// owner cards; PR 4 wires up the "Find Emails" enrichment button.
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

  if (error) {
    return (
      <div className="p-6">
        <p className="text-sm text-destructive">
          Couldn&apos;t load advisor profile: {error}
        </p>
      </div>
    );
  }

  if (data === null) {
    return <PageSpinner label="Loading advisor profile" />;
  }

  const { advisor, contacts, filings } = data;
  // Restore the user's filter/sort state on back-nav, falling back to
  // the bare list URL if no return envelope was passed.
  const returnState = parseReturnParam(searchParams.get("return"));
  const backHref = (
    returnState
      ? buildAdvisorListUrl(returnState)
      : buildAdvisorListUrl(ADVISOR_LIST_STATE_DEFAULTS)
  ) as Route;

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link
            href={backHref}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to advisors
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {advisor.name}
          </h1>
          {advisor.legal_name && advisor.legal_name !== advisor.name && (
            <p className="text-sm text-muted-foreground">
              Legal name: {advisor.legal_name}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {advisor.crd_number && <span>CRD {advisor.crd_number}</span>}
            {advisor.cik && <span>· CIK {advisor.cik}</span>}
            {advisor.sec_file_number && (
              <span>· SEC File {advisor.sec_file_number}</span>
            )}
            {(advisor.city || advisor.state) && (
              <span>
                · {[advisor.city, advisor.state].filter(Boolean).join(", ")}
              </span>
            )}
            {advisor.files_13f && (
              <Pill variant="healthy">13F filer</Pill>
            )}
          </div>
        </div>
        <ThemeToggle />
      </header>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Regulatory AUM" value={formatCurrency(advisor.regulatory_aum)} />
        <Stat
          label="Total clients"
          value={advisor.total_clients?.toLocaleString() ?? "—"}
        />
        <Stat label="Last filing" value={formatDate(advisor.last_filing_date)} />
        <Stat
          label="Latest 13F"
          value={formatDate(advisor.latest_13f_filing_date)}
        />
      </section>

      {advisor.website && (
        <section className="rounded-xl border bg-card p-4 shadow-sm">
          <h2 className="text-sm font-semibold">Website</h2>
          <a
            href={advisor.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-primary hover:underline"
          >
            {advisor.website}
          </a>
          {advisor.website_source && (
            <p className="mt-1 text-xs text-muted-foreground">
              Source: {advisor.website_source}
            </p>
          )}
        </section>
      )}

      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <h2 className="text-sm font-semibold">Form ADV details</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Item 5.G advisory activities, Item 5.D client mix, and Schedule A/B
          owner data populate after the Form ADV extractor runs (PR 3).
        </p>
        {advisor.advisory_activities && advisor.advisory_activities.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {advisor.advisory_activities.map((activity) => (
              <Pill key={activity} variant="info">
                {activity}
              </Pill>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs italic text-muted-foreground">
            No advisory activities extracted yet.
          </p>
        )}
      </section>

      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <h2 className="text-sm font-semibold">Contacts</h2>
        {contacts.length === 0 ? (
          <p className="mt-1 text-xs italic text-muted-foreground">
            No contacts on file. PR 4 wires up Apollo / Serper enrichment.
          </p>
        ) : (
          <ul className="mt-3 divide-y">
            {contacts.map((contact) => (
              <li key={contact.id} className="py-2 text-sm">
                <div className="font-medium">{contact.name}</div>
                <div className="text-xs text-muted-foreground">
                  {contact.title}
                  {contact.email && <> · {contact.email}</>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl border bg-card p-4 shadow-sm">
        <h2 className="text-sm font-semibold">Recent filings</h2>
        {filings.length === 0 ? (
          <p className="mt-1 text-xs italic text-muted-foreground">
            No filings tracked yet. PR 5 (deferred) adds the
            advisor-filing-monitor cadence.
          </p>
        ) : (
          <ul className="mt-3 divide-y">
            {filings.map((filing) => (
              <li key={filing.id} className="py-2 text-sm">
                <div className="font-medium">{filing.form_type}</div>
                <div className="text-xs text-muted-foreground">
                  {formatDate(filing.filed_at)} · {filing.summary}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
