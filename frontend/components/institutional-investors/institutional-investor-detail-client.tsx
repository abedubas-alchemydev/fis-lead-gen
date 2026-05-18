"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter } from "next/navigation";

import { ArrowLeft, ArrowRight, ExternalLink } from "lucide-react";

import {
  fetchInstitutionalInvestorProfile,
  getAdjacentInstitutionalInvestor,
} from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type {
  AdjacentResponse,
  InstitutionalInvestorProfileResponse,
} from "@/lib/types";

// Detail view for /investors/{id}. Skeleton parity with
// advisor-detail-client: topbar (breadcrumbs + h1 + meta), KPI strip,
// section panels for overview + people + filings. Adds prev/next arrows
// wired to /institutional-investors/{id}/adjacent.
export function InstitutionalInvestorDetailClient({
  investorId,
}: {
  investorId: string;
}) {
  const router = useRouter();
  const [data, setData] = useState<InstitutionalInvestorProfileResponse | null>(
    null,
  );
  const [adjacent, setAdjacent] = useState<AdjacentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchInstitutionalInvestorProfile(investorId)
      .then(setData)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load investor"),
      );
    getAdjacentInstitutionalInvestor(Number(investorId))
      .then(setAdjacent)
      .catch(() => setAdjacent(null));
  }, [investorId]);

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
        <div className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8">
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

  const { investor, contacts, filings } = data;
  const location = [investor.city, investor.state].filter(Boolean).join(", ");

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* Topbar */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard / Institutional Investors / Firm Detail
          </p>
          <h1 className="mt-1 text-[28px] font-semibold text-[var(--text-strong,#0f172a)]">
            {investor.name}
          </h1>
          {investor.legal_name && investor.legal_name !== investor.name && (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              Legal name: <span className="font-medium">{investor.legal_name}</span>
            </p>
          )}
          <p className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {investor.cik && <span>CIK {investor.cik}</span>}
            {location && <span>{location}</span>}
            {investor.advisor_id != null && (
              <Link
                href={`/advisor-list/${investor.advisor_id}` as Route}
                className="font-medium text-[var(--accent,#3b82f6)] hover:underline"
              >
                View as Investment Advisor →
              </Link>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Link
            href={"/investors" as Route}
            className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-1.5 text-sm text-[var(--text-muted,#94a3b8)] hover:bg-[var(--surface-2,#f1f6fd)]"
          >
            <ArrowLeft className="mr-1 inline h-4 w-4" />
            Back
          </Link>
          <button
            type="button"
            disabled={adjacent?.prev_id == null}
            onClick={() =>
              adjacent?.prev_id != null &&
              router.push(`/investors/${adjacent.prev_id}` as Route)
            }
            className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-1.5 text-sm disabled:opacity-40"
            title="Previous investor"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            disabled={adjacent?.next_id == null}
            onClick={() =>
              adjacent?.next_id != null &&
              router.push(`/investors/${adjacent.next_id}` as Route)
            }
            className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-1.5 text-sm disabled:opacity-40"
            title="Next investor"
          >
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* KPI strip */}
      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <KpiTile
          label="Total AUM"
          value={
            investor.total_aum != null
              ? formatCurrency(investor.total_aum)
              : "Not available"
          }
        />
        <KpiTile
          label="Holdings count"
          value={investor.holdings_count?.toLocaleString() ?? "Not available"}
        />
        <KpiTile
          label="Latest 13F"
          value={
            investor.latest_13f_filing_date
              ? formatDate(investor.latest_13f_filing_date)
              : "Not available"
          }
        />
      </div>

      {/* Section grid */}
      <div className="grid gap-4 xl:grid-cols-2">
        <SectionCard title="Overview" subtitle="Registration & web presence">
          <Field label="Status" value={investor.status} />
          <Field label="Source" value={investor.matched_source} />
          <Field
            label="Website"
            value={
              investor.website ? (
                <a
                  href={investor.website}
                  className="inline-flex items-center gap-1 text-[var(--accent,#3b82f6)] hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {investor.website}
                  <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                "Not available"
              )
            }
          />
          {investor.filings_index_url && (
            <Field
              label="EDGAR filings"
              value={
                <a
                  href={investor.filings_index_url}
                  className="text-[var(--accent,#3b82f6)] hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View filings index
                </a>
              }
            />
          )}
        </SectionCard>

        <SectionCard title="People" subtitle="Contacts">
          {contacts.length === 0 ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No contacts on file yet.
            </p>
          ) : (
            <ul className="space-y-3">
              {contacts.map((c) => (
                <li key={c.id} className="rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] p-3">
                  <div className="font-medium">{c.name}</div>
                  <div className="text-[12px] text-[var(--text-muted,#94a3b8)]">{c.title}</div>
                  {c.email && (
                    <div className="text-[12px]">
                      <a href={`mailto:${c.email}`} className="text-[var(--accent,#3b82f6)] hover:underline">
                        {c.email}
                      </a>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard title="Filings" subtitle="Recent 13F-HR filings" className="xl:col-span-2">
          {filings.length === 0 ? (
            <p className="text-sm text-[var(--text-muted,#94a3b8)]">
              No filings tracked yet.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
                <tr>
                  <th className="py-2">Form</th>
                  <th className="py-2">Filed</th>
                  <th className="py-2">Summary</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filings.map((f) => (
                  <tr key={f.id} className="border-t border-[var(--border,rgba(30,64,175,0.1))]">
                    <td className="py-2 font-mono text-[12px]">{f.form_type}</td>
                    <td className="py-2 text-[var(--text-muted,#94a3b8)]">{formatDate(f.filed_at)}</td>
                    <td className="py-2">{f.summary}</td>
                    <td className="py-2 text-right">
                      {f.source_filing_url && (
                        <a
                          href={f.source_filing_url}
                          className="text-[var(--accent,#3b82f6)] hover:underline"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function KpiTile({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-4"
      style={{
        boxShadow:
          "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
      }}
    >
      <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p className="mt-1 text-[24px] font-semibold">{value}</p>
    </div>
  );
}

function SectionCard({
  title,
  subtitle,
  className = "",
  children,
}: {
  title: string;
  subtitle?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 ${className}`}
      style={{
        boxShadow:
          "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
      }}
    >
      <h2 className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-1 text-[15px] font-medium text-[var(--text-strong,#0f172a)]">
          {subtitle}
        </p>
      )}
      <div className="mt-4 space-y-2">{children}</div>
    </section>
  );
}

function Field({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5 text-[13px]">
      <span className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}
