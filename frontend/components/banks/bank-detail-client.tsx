"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useSearchParams } from "next/navigation";

import { ExternalLink, FileText, Globe, Search, Sparkles } from "lucide-react";

import { apiRequest, ApiError } from "@/lib/api";
import { askDoxie } from "@/lib/doxie-events";
import { formatCurrency, formatDate } from "@/lib/format";
import {
  BANK_LIST_STATE_DEFAULTS,
  buildBankListUrl,
  parseReturnParam,
} from "@/lib/bank-list-state";
import {
  BankStatusPill,
  charterAuthorityLabel,
  NO_OCC_TIMELINE_EXPLANATION,
} from "@/components/banks/bank-status-pill";
import { Button } from "@/components/ui/button";
import { DetailPageSkeleton } from "@/components/ui/detail-page-skeleton";
import { Pill } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import type { BankApplicationEventItem, BankDetail } from "@/lib/types";

// Compact website display: strip protocol/www/trailing slash and take the
// host. Mirrors cleanWebsiteDisplay in advisor-detail-client.tsx.
function cleanWebsiteDisplay(website: string): string {
  return (
    website
      .replace(/^https?:\/\//i, "")
      .replace(/^www\./i, "")
      .replace(/\/+$/, "")
      .split("/")[0]
      ?.toLowerCase() ?? website
  );
}

// FDIC reports ASSET/DEP in $ thousands — scale to dollars for the shared
// compact-currency formatter so "$52,942 (thousands)" renders as "$52.9M".
function formatFdicThousands(value: number | null): string {
  return formatCurrency(value === null ? null : value * 1000);
}

// Timeline renders oldest-first (Receipt → Approved → Consummated) even
// though the BE relationship orders newest-first. NULL action dates sink to
// the bottom so an undated action never claims the "first step" slot.
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

// Detail view for /banks/{id}. Mirrors the design system used on
// /master-list/{id} and /advisor-list/{id}: page topbar with breadcrumbs +
// h1 + identifier meta line, KPI strip of MiniStat tiles, and SectionPanel
// cards on a 2-column grid.
export function BankDetailClient({ bankId }: { bankId: string }) {
  const searchParams = useSearchParams();
  const [bank, setBank] = useState<BankDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function loadBank() {
      try {
        const response = await apiRequest<BankDetail>(`/api/v1/banks/${bankId}`);
        if (active) {
          setBank(response);
          setError(null);
        }
      } catch (err) {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) {
          setError("Bank not found.");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load bank");
        }
      }
    }
    void loadBank();
    return () => {
      active = false;
    };
  }, [bankId]);

  // Restore the user's filter/sort state on back-nav, falling back to the
  // bare list URL if no return envelope was passed (deep link).
  const returnState = useMemo(
    () => parseReturnParam(searchParams.get("return")),
    [searchParams],
  );
  const backHref = (
    returnState
      ? buildBankListUrl(returnState)
      : buildBankListUrl(BANK_LIST_STATE_DEFAULTS)
  ) as Route;

  if (error) {
    return (
      <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
        <div className="rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          Couldn&apos;t load bank profile: {error}
        </div>
        <Link
          href={backHref}
          className="mt-4 inline-block text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          Back to Banks
        </Link>
      </div>
    );
  }

  if (bank === null) {
    return <DetailPageSkeleton />;
  }

  const location = [bank.city, bank.state].filter(Boolean).join(", ");
  const events = chronological(bank.application_events);
  const addressLine = [bank.address, bank.city, bank.state, bank.zip]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      {/* ── Topbar: breadcrumbs + h1 + identifier meta ───────────────────── */}
      <div className="mb-6 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={backHref}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Banks
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Institution
            Detail
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
              {bank.name}
            </h1>
            <BankStatusPill status={bank.charter_status} />
            {bank.charter_authority ? (
              <Pill variant="info">
                {charterAuthorityLabel(bank.charter_authority)} charter
              </Pill>
            ) : null}
            {bank.digital_assets ? (
              <Pill variant="self">Digital Assets</Pill>
            ) : null}
          </div>
          {/* Website + Google fallback — header-level, mirrors the sibling
              detail pages. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {bank.website ? (
              <a
                href={
                  bank.website.startsWith("http")
                    ? bank.website
                    : `https://${bank.website}`
                }
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[13px] text-[var(--accent,#6366f1)] transition hover:underline"
              >
                <Globe className="h-3.5 w-3.5" strokeWidth={2} />
                {cleanWebsiteDisplay(bank.website)}
              </a>
            ) : null}
            <a
              href={`https://www.google.com/search?q=${encodeURIComponent(`${bank.name} bank`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)] hover:underline"
            >
              <Search className="h-3.5 w-3.5" strokeWidth={2} />
              Search Google for this institution
            </a>
          </div>
          {/* Identifier strip: every federal identifier the row carries. */}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
            {bank.fdic_cert ? (
              <span>
                FDIC CERT{" "}
                <span className="font-mono text-[var(--text-dim,#475569)]">
                  {bank.fdic_cert}
                </span>
              </span>
            ) : null}
            {bank.fed_rssd ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  FED RSSD{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {bank.fed_rssd}
                  </span>
                </span>
              </>
            ) : null}
            {bank.occ_charter_number ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  OCC Charter{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {bank.occ_charter_number}
                  </span>
                </span>
              </>
            ) : null}
            {bank.occ_control_number ? (
              <>
                <span aria-hidden>·</span>
                <span>
                  OCC Control{" "}
                  <span className="font-mono text-[var(--text-dim,#475569)]">
                    {bank.occ_control_number}
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
        <div className="flex shrink-0 items-center gap-2.5">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              askDoxie({
                prompt: `Tell me about ${bank.name}${bank.fdic_cert ? ` (FDIC CERT ${bank.fdic_cert})` : ""} — summarize the charter application status and profile.`,
              })
            }
            title="Open Doxie with a question about this institution"
          >
            <Sparkles className="h-4 w-4" strokeWidth={2} />
            Ask Doxie
          </Button>
        </div>
      </div>

      {/* ── Back link — the banks BE has no /adjacent walker, so the nav row
          carries only the return link (no Prev/Next like BD/IA). ─────────── */}
      <div className="mb-5 flex items-center justify-center">
        <Link
          href={backHref}
          className="text-[12px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text,#0f172a)]"
        >
          Back to Banks
        </Link>
      </div>

      {/* ── KPI strip ───────────────────────────────────────────────────── */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Total assets"
          value={formatFdicThousands(bank.asset)}
          helper={bank.asset !== null ? "FDIC BankFind, as reported" : undefined}
        />
        <MiniStat
          label="Total deposits"
          value={formatFdicThousands(bank.deposits)}
          helper={bank.deposits !== null ? "FDIC BankFind, as reported" : undefined}
        />
        <MiniStat
          label="Offices"
          value={bank.offices?.toLocaleString() ?? "—"}
        />
        <MiniStat
          label="Established"
          value={bank.established_date ? formatDate(bank.established_date) : "—"}
          helper={
            bank.established_date === null
              ? "Not yet established — application in progress"
              : undefined
          }
        />
      </div>

      {/* ── 2-column section layout ─────────────────────────────────────── */}
      <div className="flex flex-col gap-4 xl:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* Institution profile — the FDIC/OCC registry fields. */}
          <SectionPanel eyebrow="Profile" title="Institution profile">
            <div className="grid gap-3 md:grid-cols-2">
              <MiniStat
                label="Charter authority"
                value={charterAuthorityLabel(bank.charter_authority)}
                compact
              />
              <MiniStat
                label="Bank class"
                value={bank.bkclass ?? "—"}
                compact
              />
              <MiniStat
                label="Primary regulator"
                value={bank.regulator ?? "—"}
                compact
              />
              <MiniStat
                label="FDIC insured since"
                value={bank.insured_date ? formatDate(bank.insured_date) : "—"}
                compact
              />
              <MiniStat
                label="Application received"
                value={
                  bank.application_received_date
                    ? formatDate(bank.application_received_date)
                    : "—"
                }
                compact
              />
              <MiniStat
                label="Last action"
                value={
                  bank.last_action_date ? formatDate(bank.last_action_date) : "—"
                }
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

            {/* Provenance footer: which official source(s) contributed and
                when the watcher last checked each. */}
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-dashed border-[var(--border,rgba(30,64,175,0.1))] pt-3 text-[11px] text-[var(--text-muted,#94a3b8)]">
              <span>
                Source:{" "}
                <span className="font-mono uppercase">{bank.source}</span>
              </span>
              {bank.fdic_checked_at ? (
                <span>FDIC checked {formatDate(bank.fdic_checked_at)}</span>
              ) : null}
              {bank.occ_checked_at ? (
                <span>OCC checked {formatDate(bank.occ_checked_at)}</span>
              ) : null}
            </div>
          </SectionPanel>

          {/* Official sources — every row links back to its government
              source of record (FDIC BankFind profile, OCC CAS filing page,
              digital-assets application PDFs). */}
          <SectionPanel eyebrow="Sources" title="Official source links">
            {bank.source_links.length === 0 ? (
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                No source links available yet — links appear once the watcher
                records an FDIC certificate or an OCC filing for this
                institution.
              </div>
            ) : (
              <ul className="space-y-2">
                {bank.source_links.map((link) => {
                  const isPdf = link.url.toLowerCase().endsWith(".pdf");
                  return (
                    <li key={link.url}>
                      <a
                        href={link.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-start gap-2.5 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3 transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)]"
                      >
                        {isPdf ? (
                          <FileText
                            className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                            strokeWidth={2}
                          />
                        ) : (
                          <ExternalLink
                            className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                            strokeWidth={2}
                          />
                        )}
                        <span className="min-w-0">
                          <span className="block text-[13px] font-medium text-[var(--text,#0f172a)]">
                            {link.label}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] text-[var(--text-muted,#94a3b8)]">
                            {link.url}
                          </span>
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
          </SectionPanel>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {/* Application timeline — the OCC charter-application actions
              (Receipt → Approved → Consummated), oldest first. */}
          <SectionPanel
            eyebrow="Charter Application"
            title="Application timeline"
          >
            {events.length === 0 ? (
              // Quiet empty-state. For state charters this reuses the exact
              // line the list's "Last Action" dash tooltip shows, so the two
              // surfaces explain the gap with one voice. A non-STATE bank
              // with zero events (edge: OCC match whose actions haven't
              // parsed yet) gets a neutral line instead — the state-charter
              // copy would be wrong for it.
              <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-sm text-[var(--text-muted,#94a3b8)]">
                {bank.charter_authority === "STATE"
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
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
                        {event.filing_type ? <span>{event.filing_type}</span> : null}
                        {event.source_url ? (
                          <a
                            href={event.source_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-[var(--accent,#6366f1)] hover:underline"
                          >
                            View filing
                            <ExternalLink className="h-3 w-3" strokeWidth={2} />
                          </a>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </SectionPanel>

          {/* Digital-assets application PDFs — public-portion documents from
              the OCC Digital Assets Licensing Applications page. Rendered as
              a dedicated card (in addition to the merged source-links list)
              only when the bank is tagged, so the panel never shows empty. */}
          {bank.digital_assets ? (
            <SectionPanel
              eyebrow="Digital Assets"
              title="Digital-assets licensing application"
            >
              <p className="text-sm text-[var(--text-dim,#475569)]">
                This institution appears on the OCC&apos;s Digital Assets
                Licensing Applications page. Public portions of the
                application are linked below.
              </p>
              {bank.digital_asset_pdfs.length === 0 ? (
                <p className="mt-3 text-sm text-[var(--text-muted,#94a3b8)]">
                  No public application documents have been published yet.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {bank.digital_asset_pdfs.map((pdf) => (
                    <li key={pdf.url}>
                      <a
                        href={pdf.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-start gap-2.5 rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] px-4 py-3 transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)]"
                      >
                        <FileText
                          className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
                          strokeWidth={2}
                        />
                        <span className="min-w-0">
                          <span className="block text-[13px] font-medium text-[var(--text,#0f172a)]">
                            {pdf.title ?? "Application (public portion)"}
                          </span>
                          {pdf.received_date ? (
                            <span className="mt-0.5 block text-[11px] text-[var(--text-muted,#94a3b8)]">
                              Received {formatDate(pdf.received_date)}
                            </span>
                          ) : null}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </SectionPanel>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// Inline mini-stat card. Mirrors the MiniStat helper inside
// broker-dealer-detail-client.tsx / advisor-detail-client.tsx so KPI tiles
// look identical across the detail pages.
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
        <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">{helper}</p>
      ) : null}
    </div>
  );
}
