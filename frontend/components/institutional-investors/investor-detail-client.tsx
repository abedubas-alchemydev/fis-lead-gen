"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { ArrowLeft, ExternalLink, Loader2, Sparkles } from "lucide-react";

import { ContactRow } from "@/components/master-list/detail/contact-row";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { apiRequest, ApiError } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type {
  InstitutionalInvestorProfileResponse,
  InvestorContactItem,
} from "@/lib/types";

// Detail view for /institutional-investors/{id}. Mirrors the design
// language of /advisor-list/{id}: page topbar with breadcrumbs + h1 +
// meta line, KPI strip of MiniStat tiles, and SectionPanel cards.
//
// Unlike the advisor / BD detail pages, institutional investors don't
// have a pre-populated officer list (no FINRA / Form ADV equivalent for
// pure-13F filers). The "Generate More Details" form is a single-name
// input that POSTs to /enrich with one officer in the body -- PDL is the
// primary provider in the discovery chain (PR #458), so a name + the
// firm's domain is enough to land a hit with multi-value emails / phones
// in most cases.
export function InvestorDetailClient({
  investorId,
}: {
  investorId: string;
}) {
  const [data, setData] = useState<InstitutionalInvestorProfileResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiRequest<InstitutionalInvestorProfileResponse>(
      `/api/v1/institutional-investors/${investorId}/profile`,
    )
      .then(setData)
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Failed to load investor",
        );
      });
  }, [investorId]);

  function handleContactsUpdated(updated: InvestorContactItem[]) {
    setData((d) => (d ? { ...d, contacts: updated } : d));
  }

  function handleContactRowUpdated(updated: InvestorContactItem) {
    setData((d) => {
      if (!d) return d;
      const contacts = d.contacts.map((c) =>
        c.id === updated.id ? updated : c,
      );
      return { ...d, contacts };
    });
  }

  if (error) {
    return (
      <div className="px-7 pb-12 pt-7 lg:px-9">
        <div className="rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          Couldn&apos;t load investor profile: {error}
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
            {Array.from({ length: 3 }).map((_, i) => (
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

  const { investor, contacts, filings } = data;
  const location = [investor.city, investor.state].filter(Boolean).join(", ");

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* ── Topbar ──────────────────────────────────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            Institutional Investors{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Firm Detail
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            {investor.name}
          </h1>
          {investor.legal_name && investor.legal_name !== investor.name ? (
            <p className="mt-1 text-[13px] text-[var(--text-dim,#475569)]">
              Legal name:{" "}
              <span className="font-medium text-[var(--text,#0f172a)]">
                {investor.legal_name}
              </span>
            </p>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {investor.cik ? (
              <span>
                CIK{" "}
                <span className="font-mono text-[var(--text-dim,#475569)]">
                  {investor.cik}
                </span>
              </span>
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

      {investor.advisor_id ? (
        <div className="mb-5">
          <Link
            href={`/advisor-list/${investor.advisor_id}`}
            className="inline-flex items-center gap-1.5 text-[12px] text-[var(--accent,#6366f1)] hover:underline"
          >
            View as Investment Advisor
            <ExternalLink className="h-3 w-3" strokeWidth={2} />
          </Link>
        </div>
      ) : null}

      {/* ── KPI strip ───────────────────────────────────────────── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Total AUM (13F)"
          value={formatCurrency(investor.total_aum)}
        />
        <MiniStat
          label="Holdings"
          value={investor.holdings_count?.toLocaleString() ?? "—"}
        />
        <MiniStat
          label="Latest 13F"
          value={formatDate(investor.latest_13f_filing_date)}
        />
        <MiniStat label="Status" value={investor.status || "—"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {/* People panel */}
        <SectionPanel eyebrow="People" title="Owners, officers, and contacts">
          <EnrichForm
            investorId={Number(investorId)}
            existingNames={contacts.map((c) => c.name)}
            onContactsUpdated={handleContactsUpdated}
          />
          <div className="mt-4 space-y-2">
            {contacts.length === 0 ? (
              <p className="text-sm text-[var(--text-muted,#94a3b8)]">
                No contacts yet. Use the form above to look one up by name.
              </p>
            ) : (
              contacts.map((contact) => (
                <div
                  key={`contact-${contact.id}`}
                  className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-3 text-sm text-[var(--text-dim,#475569)]"
                >
                  <p className="font-semibold text-[var(--text,#0f172a)]">
                    {contact.name}
                  </p>
                  {contact.title ? (
                    <p className="mt-1">{contact.title}</p>
                  ) : null}
                  <ContactRow
                    entityKind="investor"
                    entityId={Number(investorId)}
                    entityName={investor.name}
                    contact={contact}
                    onContactUpdated={handleContactRowUpdated}
                  />
                </div>
              ))
            )}
          </div>
        </SectionPanel>

        {/* Filings panel */}
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

      <div className="mt-6">
        <Link
          href="/investors"
          className="inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
          Back to investors
        </Link>
      </div>
    </div>
  );
}

// Single-input form for the "Generate More Details" flow on the investor
// detail page. Submits one officer name to /enrich; the chain (pdl first,
// then apollo/hunter/snov) resolves it into an InvestorContact row whose
// emails/phones JSONB arrays power the ContactRow rendering above.
function EnrichForm({
  investorId,
  existingNames,
  onContactsUpdated,
}: {
  investorId: number;
  existingNames: string[];
  onContactsUpdated: (updated: InvestorContactItem[]) => void;
}) {
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [inFlight, setInFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    const parts = trimmed.split(/\s+/);
    if (parts.length < 2) {
      setError("Enter a full name (first + last).");
      return;
    }
    if (
      existingNames.some(
        (n) => n.trim().toLowerCase() === trimmed.toLowerCase(),
      )
    ) {
      setError("Already enriched.");
      return;
    }
    setError(null);
    setInFlight(true);
    try {
      const first = parts[0];
      const last = parts.slice(1).join(" ");
      const contacts = await apiRequest<InvestorContactItem[]>(
        `/api/v1/institutional-investors/${investorId}/enrich`,
        {
          method: "POST",
          body: JSON.stringify({
            officers: [
              {
                type: "person",
                first_name: first,
                last_name: last,
                title: title.trim() || null,
              },
            ],
          }),
        },
      );
      onContactsUpdated(contacts);
      setName("");
      setTitle("");
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.detail || `Request failed (${e.status})`);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setInFlight(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2">
      <input
        type="text"
        placeholder="Officer name (first + last)"
        value={name}
        onChange={(e) => setName(e.target.value)}
        disabled={inFlight}
        className="min-w-[180px] flex-1 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-2 py-1 text-[12px] text-[var(--text,#0f172a)] focus:border-[var(--accent,#6366f1)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
      <input
        type="text"
        placeholder="Title (optional)"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={inFlight}
        className="min-w-[140px] flex-1 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-2 py-1 text-[12px] text-[var(--text,#0f172a)] focus:border-[var(--accent,#6366f1)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={inFlight || !name.trim()}
        className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-2.5 py-1 text-[12px] text-[var(--text-dim,#475569)] transition hover:border-[var(--accent,#6366f1)] hover:text-[var(--accent,#6366f1)] disabled:cursor-not-allowed disabled:opacity-60"
      >
        {inFlight ? (
          <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
        ) : (
          <Sparkles className="h-3 w-3" strokeWidth={2} />
        )}
        {inFlight ? "Finding…" : "Generate More Details"}
      </button>
      {error ? (
        <p className="basis-full text-[11px] text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </p>
      ) : null}
    </form>
  );
}

function MiniStat({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-4 text-sm">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p className="mt-1 text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
        {value}
      </p>
    </div>
  );
}
