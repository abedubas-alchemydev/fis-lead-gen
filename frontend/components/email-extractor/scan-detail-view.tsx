"use client";

import {
  AlertCircle,
  Bookmark,
  BookmarkCheck,
  CheckCircle2,
  Loader2,
  MailPlus,
  Search,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ChannelIconCell } from "@/components/advisor-list/channel-icon-cell";
import { EmailExtractorErrorCard } from "@/components/email-extractor/email-extractor-error-card";
import { EmptyScanResultsState } from "@/components/email-extractor/empty-scan-results-state";
import { EnrichAllButton } from "@/components/email-extractor/enrich-all-button";
import {
  RowsPerPageSelect,
  type RowsPerPageValue,
} from "@/components/email-extractor/rows-per-page-select";
import { ScanResultsLoading } from "@/components/email-extractor/scan-results-loading";
import { OutreachModal } from "@/components/master-list/outreach-modal";
import { Button } from "@/components/ui/button";
import { Pill, type PillVariant } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { ApiError } from "@/lib/api";
import {
  deleteSavedContact,
  listSavedContacts,
  saveContact,
} from "@/lib/saved-contacts";
import type { SavedContact } from "@/types/saved-contact";
import { formatDate, formatRelativeTime } from "@/lib/format";
import {
  enrichEmail,
  getScan,
  getVerifyRun,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
  TERMINAL_STATUSES,
  verifyEmail,
  type DiscoveredEmailResponse,
  type EmailVerificationResponse,
  type RunStatus,
  type ScanResponse,
  type VerifyResultItem,
} from "@/lib/email-extractor";

// Run-status pill mapping mirrors /email-extractor (hub) so hub, standalone
// scan page, and inline master-list section visually agree on the four states.
const STATUS_PILL_VARIANT: Record<RunStatus, PillVariant> = {
  queued: "unknown",
  running: "info",
  completed: "healthy",
  failed: "critical",
};

const STATUS_PILL_LABEL: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

const STATUS_DOT_CLASS: Record<RunStatus, string> = {
  queued:
    "bg-[var(--text-muted,#94a3b8)] shadow-[0_0_0_4px_rgba(148,163,184,0.15)]",
  running:
    "bg-[var(--blue,#3b82f6)] shadow-[0_0_0_4px_rgba(59,130,246,0.15)] animate-pulse",
  completed:
    "bg-[var(--green,#10b981)] shadow-[0_0_0_4px_rgba(16,185,129,0.15)]",
  failed: "bg-[var(--red,#ef4444)] shadow-[0_0_0_4px_rgba(239,68,68,0.15)]",
};

// SMTP verification → Pill variant + label + icon. Same affordance the rest
// of the design system uses (master-list detail pills).
const SMTP_STATUS_STYLES: Record<
  string,
  { variant: PillVariant; Icon: typeof CheckCircle2; label: string }
> = {
  deliverable: { variant: "healthy", Icon: CheckCircle2, label: "Deliverable" },
  undeliverable: { variant: "critical", Icon: XCircle, label: "Undeliverable" },
  inconclusive: { variant: "warning", Icon: AlertCircle, label: "Inconclusive" },
  blocked: { variant: "unknown", Icon: AlertCircle, label: "Blocked" },
};

// A scan can surface hundreds of discovered emails; page the results table so
// the DOM stays bounded and the panel doesn't grow into an endless scroll.
// The page size is now user-selectable (25 / 50 / 100 / All) and persisted to
// localStorage so the choice survives reloads and applies to the next scan.
const ROWS_PER_PAGE_STORAGE_KEY = "email-extractor:rows-per-page";
const DEFAULT_ROWS_PER_PAGE = 100;

// All discovered-email rows share this `source` when saved via the Save
// Contact toggle, so saved-state hydration only pulls this slice of the
// user's saved contacts.
const SAVE_CONTACT_SOURCE = "discovered_email";

// Read the persisted rows-per-page choice. Returns null (→ caller defaults to
// 100) when unset, on a bad value, or during SSR where localStorage is absent.
function readStoredPageSize(): RowsPerPageValue | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ROWS_PER_PAGE_STORAGE_KEY);
    if (raw === null) return null;
    if (raw === "all") return "all";
    const parsed = Number(raw);
    // Number.isInteger also rejects NaN/Infinity, so it subsumes the old
    // isFinite guard while additionally rejecting a tampered fractional value
    // like "50.5" (which isFinite would have wrongly accepted).
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
}

// Persist the rows-per-page choice: the raw number, or the string "all".
function writeStoredPageSize(value: RowsPerPageValue): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ROWS_PER_PAGE_STORAGE_KEY, String(value));
  } catch {
    /* storage unavailable — non-fatal, choice simply won't persist */
  }
}

// After a per-row Enrich that requested a phone reveal, poll for the async
// number to land (Apollo calls our webhook seconds-to-minutes later). Bounded
// so we stop when a reveal never resolves rather than polling forever.
const PHONE_REVEAL_POLL_MS = 3000;
// The backend self-clears `phone_reveal_pending` via a ~15-min TTL, so the poll
// also terminates when the flag flips (see the stop predicate in
// startPhoneRevealPoll). This hard cap is a safety net for an active viewer:
// raised from 90s to 4 min so a reveal that lands a couple minutes out still
// resolves in-place without a manual reload, while still bounding how long each
// open tab keeps refetching the whole scan on a cadence.
const PHONE_REVEAL_TIMEOUT_MS = 240_000;

// ── Helpers ──────────────────────────────────────────────────────────────

function formatConfidence(c: number | null): React.ReactNode {
  if (c === null || Number.isNaN(c)) {
    return <span title="No confidence score from source">—</span>;
  }
  return `×${c.toFixed(2)}`;
}

function latestVerification(
  fromPoll: EmailVerificationResponse[],
  fromLocal: EmailVerificationResponse | undefined
): EmailVerificationResponse | undefined {
  const candidates: EmailVerificationResponse[] = [...fromPoll];
  if (fromLocal) candidates.push(fromLocal);
  if (candidates.length === 0) return undefined;
  return candidates.reduce((latest, current) =>
    new Date(current.checked_at) > new Date(latest.checked_at) ? current : latest
  );
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// Merge a one-shot saved-contacts fetch into the live saved-state map WITHOUT
// clobbering rows the user has already saved/un-saved this session. The fetch
// is a mount-time snapshot that can resolve AFTER an in-flight Save/un-save,
// so for every contact_id the user has touched (tracked in touchedContactIds)
// we keep the current local value — present ⇒ its saved_id wins, absent ⇒ the
// un-save wins — and only take server state for untouched rows. Untouched rows
// therefore behave exactly as the old full-replace did.
function mergeFetchedPreservingTouched(
  fetched: SavedContact[],
  current: Map<number, number>,
  touchedContactIds: Set<number>
): Map<number, number> {
  const next = new Map<number, number>();
  // Server snapshot drives every contact the user hasn't locally touched.
  for (const contact of fetched) {
    if (!touchedContactIds.has(contact.contact_id)) {
      next.set(contact.contact_id, contact.id);
    }
  }
  // Local state wins for touched contacts: re-apply a just-saved id, and leave
  // a just-un-saved contact absent (delete wins) by simply not setting it.
  for (const contactId of touchedContactIds) {
    const localValue = current.get(contactId);
    if (localValue !== undefined) {
      next.set(contactId, localValue);
    }
  }
  return next;
}

// Per-cause copy for a failed per-row enrich. Mirrors the outreach modal's
// error mapping so a 503/502/404 reads as something actionable rather than the
// old blanket "couldn't enrich" toast that hid the real cause.
function enrichErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) {
      return "Enrichment isn't configured — contact an admin.";
    }
    if (err.status === 502) {
      return "Couldn't reach the enrichment service — try again in a moment.";
    }
    if (err.status === 404) {
      return "This email no longer exists — refresh the scan.";
    }
  }
  return errorMessage(err, "Couldn't enrich — please try again.");
}

// Map raw BE error strings into per-cause copy so the user gets an
// actionable subtext rather than a stack-trace-style message. Falls
// back to the raw message if the BE didn't tag the cause.
function dynamicScanErrorSubtext(rawMessage: string | null): string {
  if (rawMessage === null) {
    return "Something went wrong while running this scan.";
  }
  const lower = rawMessage.toLowerCase();
  if (lower.includes("no domain")) {
    return "No domain on file for this firm — try the hub with a domain entered manually.";
  }
  if (lower.includes("rate") && lower.includes("limit")) {
    return "Rate limited — wait a minute and retry.";
  }
  if (lower.includes("apollo")) {
    return "Enrichment is unavailable right now — emails were discovered but couldn't be enriched.";
  }
  return rawMessage;
}

// ── Sub-components ───────────────────────────────────────────────────────

function StatusPill({ status }: { status: string }): React.ReactElement | null {
  const cfg = SMTP_STATUS_STYLES[status];
  if (!cfg) return null;
  const { variant, Icon, label } = cfg;
  return (
    <Pill variant={variant}>
      <Icon className="h-3 w-3" strokeWidth={2.5} aria-hidden />
      {label}
    </Pill>
  );
}

function VerifyButton({
  emailId,
  inFlight,
  onClick,
  error,
}: {
  emailId: number;
  inFlight: boolean;
  onClick: (emailId: number) => void;
  error: string | undefined;
}): React.ReactElement {
  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onClick(emailId)}
        disabled={inFlight}
      >
        {inFlight ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
        ) : null}
        {inFlight ? "Verifying…" : "Verify"}
      </Button>
      {error ? (
        <span className="text-[11px] text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function VerificationCell({
  row,
  localVerification,
  inFlight,
  verifyError,
  onVerify,
}: {
  row: DiscoveredEmailResponse;
  localVerification: EmailVerificationResponse | undefined;
  inFlight: boolean;
  verifyError: string | undefined;
  onVerify: (emailId: number) => void;
}): React.ReactElement {
  const latest = latestVerification(row.verifications, localVerification);

  if (latest && latest.smtp_status !== "not_checked") {
    return <StatusPill status={latest.smtp_status} />;
  }

  const syntaxOk = latest?.syntax_valid === true;
  const mxOk = latest?.mx_record_present === true;
  return (
    <div className="flex flex-col items-start gap-1">
      <span className="inline-flex items-center gap-2 text-[11px] text-[var(--text-dim,#475569)]">
        <span className="inline-flex items-center gap-1">
          {syntaxOk ? (
            <CheckCircle2
              className="h-3.5 w-3.5 text-[var(--green,#10b981)]"
              strokeWidth={2}
            />
          ) : (
            <XCircle
              className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]"
              strokeWidth={2}
            />
          )}
          syntax
        </span>
        <span className="inline-flex items-center gap-1">
          {mxOk ? (
            <CheckCircle2
              className="h-3.5 w-3.5 text-[var(--green,#10b981)]"
              strokeWidth={2}
            />
          ) : (
            <XCircle
              className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]"
              strokeWidth={2}
            />
          )}
          MX
        </span>
      </span>
      <VerifyButton
        emailId={row.id}
        inFlight={inFlight}
        onClick={onVerify}
        error={verifyError}
      />
    </div>
  );
}

function EnrichErrorText({ error }: { error: string }): React.ReactElement {
  return (
    <span className="text-[11px] text-[var(--pill-red-text,#b91c1c)]">
      {error}
    </span>
  );
}

function EnrichmentCell({
  row,
  inFlight,
  error,
  onEnrich,
}: {
  row: DiscoveredEmailResponse;
  inFlight: boolean;
  error: string | undefined;
  onEnrich: (emailId: number) => void;
}): React.ReactElement {
  if (row.enrichment_status === "enriched") {
    // The discovered address keyed the lookup, so only surface a revealed
    // email when it's actually a different (e.g. personal) address. We do NOT
    // feed the discovered `row.email` into the Email channel — it already lives
    // in the EMAIL column — so that icon greys out unless enrichment found a
    // genuinely different (personal/alternate) address.
    const revealedEmail =
      row.enriched_email && row.enriched_email !== row.email
        ? row.enriched_email
        : null;
    // The three channels render via the shared ChannelIconCell so this column
    // matches the BD/investor detail pages pixel-for-pixel (LinkedIn / Email /
    // Phone slots, accent + clickable when present, greyed when absent).
    const showPhonePending = !!row.phone_reveal_pending && !row.enriched_phone;
    return (
      <div className="flex flex-col items-start gap-1 text-[12px]">
        {row.enriched_name ? (
          <span className="font-semibold text-[var(--text,#0f172a)]">
            {row.enriched_name}
          </span>
        ) : null}
        {row.enriched_title ? (
          <span className="text-[var(--text-dim,#475569)]">
            {row.enriched_title}
          </span>
        ) : null}
        {row.enriched_company ? (
          <span className="text-[var(--text-muted,#94a3b8)]">
            {row.enriched_company}
          </span>
        ) : null}
        <ChannelIconCell
          contact={{
            id: row.id,
            linkedin_url: row.enriched_linkedin_url,
            email: revealedEmail,
            phone: row.enriched_phone,
          }}
        />
        {/* ChannelIconCell has no pending state, so the async Apollo phone
            reveal keeps its own "finding phone…" signal beneath the icon row
            until the number lands (the row poll clears phone_reveal_pending). */}
        {showPhonePending ? (
          <span className="inline-flex items-center gap-1 text-[var(--text-muted,#94a3b8)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            finding phone…
          </span>
        ) : null}
      </div>
    );
  }

  if (row.enrichment_status === "no_match") {
    // An honest miss, but still re-runnable on its own: the chain or a
    // provider's coverage may have changed since the last attempt, so give the
    // row the same per-row re-run affordance the error row gets (bulk "Enrich
    // All" already retries no_match rows too).
    return (
      <div className="flex flex-col items-start gap-1">
        <Pill variant="unknown">Not found</Pill>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onEnrich(row.id)}
          disabled={inFlight}
        >
          {inFlight ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : null}
          {inFlight ? "Enriching…" : "Re-enrich"}
        </Button>
        {error ? <EnrichErrorText error={error} /> : null}
      </div>
    );
  }

  if (row.enrichment_status === "error") {
    return (
      <div className="flex flex-col items-start gap-1">
        <Pill variant="critical">
          <AlertCircle className="h-3 w-3" strokeWidth={2.5} aria-hidden /> Error
        </Pill>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onEnrich(row.id)}
          disabled={inFlight}
        >
          {inFlight ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : null}
          Retry
        </Button>
        {error ? <EnrichErrorText error={error} /> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onEnrich(row.id)}
        disabled={inFlight}
      >
        {inFlight ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
        ) : null}
        {inFlight ? "Enriching…" : "Enrich"}
      </Button>
      {error ? <EnrichErrorText error={error} /> : null}
    </div>
  );
}

// Per-row "Outreach" action. A discovered email has no firm/contact record,
// so it reuses the ad-hoc outreach pipeline (free-form recipient) -- the same
// path the Form 4 insider feed uses. The discovered address is always the
// recipient; enrichment just lets the AI draft open with a real name/title.
function OutreachCell({
  row,
}: {
  row: DiscoveredEmailResponse;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button type="button" size="sm" onClick={() => setOpen(true)}>
        <MailPlus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        Outreach
      </Button>
      {open ? (
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
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

// Per-row "Save Contact" toggle. Self-contained like OutreachCell: it owns its
// in-flight + inline-error state, while the persisted saved-state (contact_id →
// saved_id) lives in the parent map so it survives paging + re-render. Clicking
// Save persists the row under the discovered_email source and stores the
// returned saved_id; clicking Saved un-saves by that id. The un-save direction
// is optimistic with rollback (we already hold the id); the save direction
// awaits the server for the new id — matching the verify/enrich in-flight +
// inline-error conventions used elsewhere in this file.
function SaveContactCell({
  row,
  savedId,
  onSavedChange,
}: {
  row: DiscoveredEmailResponse;
  savedId: number | null;
  onSavedChange: (contactId: number, savedId: number | null) => void;
}): React.ReactElement {
  const [inFlight, setInFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isSaved = savedId !== null;

  const handleClick = useCallback(async () => {
    if (inFlight) return;
    setError(null);
    setInFlight(true);
    if (savedId !== null) {
      // Un-save — optimistically clear the map, roll back on failure.
      onSavedChange(row.id, null);
      try {
        await deleteSavedContact(savedId);
      } catch (err) {
        // A 404 means the row is already gone server-side (e.g. it was removed
        // from the Saved Contacts page in another tab). Our desired end state —
        // not saved — already holds, so treat it as success: keep the optimistic
        // clear, no rollback, no error. Roll back + surface only real failures.
        if (!(err instanceof ApiError && err.status === 404)) {
          onSavedChange(row.id, savedId);
          setError(errorMessage(err, "Couldn't remove — try again."));
        }
      } finally {
        setInFlight(false);
      }
      return;
    }
    // Save — await the server for the new saved_id, then commit it to the map.
    try {
      const created = await saveContact({
        source: SAVE_CONTACT_SOURCE,
        contact_id: row.id,
      });
      onSavedChange(row.id, created.id);
    } catch (err) {
      setError(errorMessage(err, "Couldn't save — try again."));
    } finally {
      setInFlight(false);
    }
  }, [inFlight, savedId, row.id, onSavedChange]);

  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        type="button"
        variant={isSaved ? "primary" : "outline"}
        size="sm"
        onClick={() => void handleClick()}
        disabled={inFlight}
        aria-pressed={isSaved}
      >
        {inFlight ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
        ) : isSaved ? (
          <BookmarkCheck className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        ) : (
          <Bookmark className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        )}
        {isSaved ? "Saved" : "Save"}
      </Button>
      {error ? (
        <span className="text-[11px] text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function ResultsTable({
  rows,
  localVerifications,
  verifyInFlight,
  verifyErrors,
  onVerify,
  enrichInFlight,
  enrichErrors,
  onEnrich,
  savedContactIds,
  onSavedChange,
}: {
  rows: DiscoveredEmailResponse[];
  localVerifications: Record<number, EmailVerificationResponse>;
  verifyInFlight: Set<number>;
  verifyErrors: Record<number, string>;
  onVerify: (emailId: number) => void;
  enrichInFlight: Set<number>;
  enrichErrors: Record<number, string>;
  onEnrich: (emailId: number) => void;
  // contact_id → saved_id for rows the user has saved. Absent key = not saved.
  savedContactIds: Map<number, number>;
  onSavedChange: (contactId: number, savedId: number | null) => void;
}): React.ReactElement {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]">
      <table className="w-full text-[13px]">
        <thead className="bg-[var(--surface-2,#f1f6fd)] text-left">
          <tr>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Email
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Confidence
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Enrichment
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Verification
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Outreach
            </th>
            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              Save
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
          {rows.map((row) => (
            <tr key={row.id} className="align-top">
              <td className="px-4 py-3 font-mono text-[12px] text-[var(--text,#0f172a)]">
                {row.email}
              </td>
              <td className="px-4 py-3 text-[12px] tabular-nums text-[var(--text-dim,#475569)]">
                {formatConfidence(row.confidence)}
              </td>
              <td className="px-4 py-3">
                <EnrichmentCell
                  row={row}
                  inFlight={enrichInFlight.has(row.id)}
                  error={enrichErrors[row.id]}
                  onEnrich={onEnrich}
                />
              </td>
              <td className="px-4 py-3">
                <VerificationCell
                  row={row}
                  localVerification={localVerifications[row.id]}
                  inFlight={verifyInFlight.has(row.id)}
                  verifyError={verifyErrors[row.id]}
                  onVerify={onVerify}
                />
              </td>
              <td className="px-4 py-3">
                <OutreachCell row={row} />
              </td>
              <td className="px-4 py-3">
                <SaveContactCell
                  row={row}
                  savedId={savedContactIds.get(row.id) ?? null}
                  onSavedChange={onSavedChange}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultsPager({
  page,
  pageSize,
  total,
  onPrev,
  onNext,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}): React.ReactElement {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = page * pageSize;
  return (
    <div className="flex items-center justify-end gap-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
      <button
        type="button"
        onClick={onPrev}
        disabled={page === 0}
        className="rounded-md px-2 py-1 transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
        aria-label="Previous page of results"
      >
        Prev
      </button>
      <span aria-live="polite" className="tabular-nums">
        {start + 1}–{Math.min(start + pageSize, total)} of {total}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={page >= totalPages - 1}
        className="rounded-md px-2 py-1 transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
        aria-label="Next page of results"
      >
        Next
      </button>
    </div>
  );
}

function MiniStat({
  label,
  value,
  helper,
  valueTitle,
}: {
  label: string;
  value: string;
  helper?: string;
  valueTitle?: string;
}): React.ReactElement {
  return (
    <div className="rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3.5 py-2.5">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        {label}
      </p>
      <p
        className="mt-1 break-words text-[13px] font-semibold text-[var(--text,#0f172a)]"
        title={valueTitle}
      >
        {value}
      </p>
      {helper ? (
        <p className="mt-0.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
          {helper}
        </p>
      ) : null}
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────────

export interface ScanDetailViewProps {
  scanId: number;
  // Toggleable for the inline master-list section, which renders inside a
  // SectionPanel and doesn't need the run-metadata grid.
  showRunMetadata?: boolean;
  // Surface the loaded scan upward — lets the standalone page render its
  // h1 ("{scan.domain}") and topbar meta from the same fetch this view
  // already polls.
  onScanLoaded?: (scan: ScanResponse) => void;
}

export function ScanDetailView({
  scanId,
  showRunMetadata = true,
  onScanLoaded,
}: ScanDetailViewProps): React.ReactElement {
  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const [localVerifications, setLocalVerifications] = useState<
    Record<number, EmailVerificationResponse>
  >({});
  const [verifyInFlight, setVerifyInFlight] = useState<Set<number>>(new Set());
  const [verifyErrors, setVerifyErrors] = useState<Record<number, string>>({});
  const [enrichInFlight, setEnrichInFlight] = useState<Set<number>>(new Set());
  const [enrichErrors, setEnrichErrors] = useState<Record<number, string>>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  // User-selectable rows-per-page (persisted to localStorage). "all" resolves
  // to the total row count below so the slice returns everything and the pager
  // hides. Deliberately NOT reset on scanId change — it's a viewing preference
  // that should carry across scans.
  const [pageSize, setPageSize] = useState<RowsPerPageValue>(
    () => readStoredPageSize() ?? DEFAULT_ROWS_PER_PAGE
  );
  // Saved-contact state: contact_id (a discovered-email id) → saved_id. Hydrated
  // once on mount from the user's saved "discovered_email" contacts, then
  // mutated optimistically by each SaveContactCell via handleSavedChange.
  const [savedContactIds, setSavedContactIds] = useState<Map<number, number>>(
    new Map()
  );
  // contact_ids the user has saved/un-saved locally this session. The one-shot
  // mount hydration merges against this set (local wins) so a Save/un-save that
  // resolves BEFORE the initial GET isn't clobbered by the older server
  // snapshot. A ref (not state) because it's a write-log the hydration reads
  // synchronously, not something that should trigger a re-render.
  const touchedContactIdsRef = useRef<Set<number>>(new Set());

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef<number>(0);
  const verifyPollsRef = useRef<Map<number, ReturnType<typeof setInterval>>>(
    new Map()
  );
  // Per-row phone-reveal polls (post-enrich), keyed by discovered-email id.
  const enrichPollsRef = useRef<Map<number, ReturnType<typeof setInterval>>>(
    new Map()
  );

  // Stash the latest onScanLoaded so the polling effect doesn't re-mount on
  // every parent render — keeps the 1.5s interval stable across re-renders.
  const onScanLoadedRef = useRef(onScanLoaded);
  useEffect(() => {
    onScanLoadedRef.current = onScanLoaded;
  }, [onScanLoaded]);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const stopVerifyPoll = useCallback((emailId: number) => {
    const handle = verifyPollsRef.current.get(emailId);
    if (handle !== undefined) {
      clearInterval(handle);
      verifyPollsRef.current.delete(emailId);
    }
  }, []);

  const stopAllVerifyPolls = useCallback(() => {
    verifyPollsRef.current.forEach((handle) => clearInterval(handle));
    verifyPollsRef.current.clear();
  }, []);

  const stopEnrichPoll = useCallback((emailId: number) => {
    const handle = enrichPollsRef.current.get(emailId);
    if (handle !== undefined) {
      clearInterval(handle);
      enrichPollsRef.current.delete(emailId);
    }
  }, []);

  const stopAllEnrichPolls = useCallback(() => {
    enrichPollsRef.current.forEach((handle) => clearInterval(handle));
    enrichPollsRef.current.clear();
  }, []);

  useEffect(
    () => () => {
      stopPolling();
      stopAllVerifyPolls();
      stopAllEnrichPolls();
    },
    [stopPolling, stopAllVerifyPolls, stopAllEnrichPolls]
  );

  const updateScan = useCallback((next: ScanResponse) => {
    setScan(next);
    onScanLoadedRef.current?.(next);
  }, []);

  // Mutate the saved-state map. Passed down to every SaveContactCell so a
  // save/un-save reflects immediately (and survives paging, since the map
  // lives here, not in the cell). `null` un-saves (drops the key).
  const handleSavedChange = useCallback(
    (contactId: number, savedId: number | null) => {
      // Record the local mutation so the mount hydration won't overwrite this
      // contact with an older server snapshot (see mergeFetchedPreservingTouched
      // + the hydration effect below). Covers both directions — save and
      // un-save both flow through here.
      touchedContactIdsRef.current.add(contactId);
      setSavedContactIds((prev) => {
        const next = new Map(prev);
        if (savedId === null) next.delete(contactId);
        else next.set(contactId, savedId);
        return next;
      });
    },
    []
  );

  // Hydrate initial saved-state once on mount. Saved "discovered_email"
  // contacts are user-scoped (not scan-scoped) and keyed by the discovered-
  // email id, which is unique across scans, so one fetch covers every row the
  // view will ever render. Failure is swallowed — the Save toggles still work,
  // they just start from an un-hydrated (all-unsaved) baseline.
  useEffect(() => {
    let active = true;
    listSavedContacts(SAVE_CONTACT_SOURCE)
      .then((contacts) => {
        if (!active) return;
        // MERGE, don't replace: the functional updater composes against the
        // latest map, and mergeFetchedPreservingTouched keeps any row the user
        // has already toggled this session (local wins) so a Save/un-save that
        // beat this GET to resolve isn't dropped by the stale snapshot.
        setSavedContactIds((prev) =>
          mergeFetchedPreservingTouched(
            contacts,
            prev,
            touchedContactIdsRef.current
          )
        );
      })
      .catch(() => {
        /* swallow — saved-state simply stays un-hydrated */
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!scanId || Number.isNaN(scanId)) {
      setLoadError("Invalid scan id");
      return;
    }
    // Reset state when scanId changes (e.g., user clicks Find Emails to start
    // a new scan — same component, new scanId).
    setScan(null);
    setLoadError(null);
    setTimedOut(false);
    setLocalVerifications({});
    setVerifyInFlight(new Set());
    setVerifyErrors({});
    setEnrichInFlight(new Set());
    setEnrichErrors({});
    setSearchQuery("");
    setPage(0);
    stopPolling();
    stopAllEnrichPolls();

    let active = true;

    async function load() {
      try {
        const response = await getScan(scanId);
        if (!active) return;
        updateScan(response);
        startedAtRef.current = Date.now();
        if (!TERMINAL_STATUSES.has(response.status)) {
          pollRef.current = setInterval(async () => {
            try {
              const next = await getScan(scanId);
              updateScan(next);
              if (TERMINAL_STATUSES.has(next.status)) {
                stopPolling();
                return;
              }
              if (Date.now() - startedAtRef.current > POLL_TIMEOUT_MS) {
                stopPolling();
                setTimedOut(true);
              }
            } catch (pollErr) {
              stopPolling();
              setLoadError(errorMessage(pollErr, "polling failed"));
            }
          }, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (active) setLoadError(errorMessage(err, "Could not load scan"));
      }
    }

    void load();
    return () => {
      active = false;
    };
  }, [scanId, stopPolling, stopAllEnrichPolls, updateScan]);

  const refetchScan = useCallback(async () => {
    if (!scanId || Number.isNaN(scanId)) return;
    try {
      const response = await getScan(scanId);
      updateScan(response);
    } catch {
      // The EnrichAllButton surfaces its own polling errors; swallow here to
      // avoid stomping on the inline load error banner with transient blips.
    }
  }, [scanId, updateScan]);

  const isInFlight =
    scan !== null && !TERMINAL_STATUSES.has(scan.status) && !timedOut;
  const unenrichedCount =
    scan?.discovered_emails.filter(
      (row) => row.enrichment_status !== "enriched"
    ).length ?? 0;

  const filteredEmails = useMemo(() => {
    if (!scan) return [];
    const q = searchQuery.trim().toLowerCase();
    if (!q) return scan.discovered_emails;
    return scan.discovered_emails.filter(
      (row) =>
        row.email.toLowerCase().includes(q) ||
        (row.enriched_name?.toLowerCase().includes(q) ?? false) ||
        (row.enriched_title?.toLowerCase().includes(q) ?? false) ||
        (row.enriched_company?.toLowerCase().includes(q) ?? false) ||
        (row.enriched_phone?.toLowerCase().includes(q) ?? false)
    );
  }, [scan, searchQuery]);

  const finishVerify = useCallback(
    (
      emailId: number,
      result: VerifyResultItem | undefined,
      failureMessage: string | null
    ) => {
      stopVerifyPoll(emailId);
      if (result) {
        const verification: EmailVerificationResponse = {
          id: -1,
          syntax_valid: null,
          mx_record_present: null,
          smtp_status: result.smtp_status,
          smtp_message: result.smtp_message,
          checked_at: result.checked_at,
        };
        setLocalVerifications((prev) => ({ ...prev, [emailId]: verification }));
      }
      if (failureMessage !== null) {
        setVerifyErrors((prev) => ({ ...prev, [emailId]: failureMessage }));
      }
      setVerifyInFlight((prev) => {
        const next = new Set(prev);
        next.delete(emailId);
        return next;
      });
    },
    [stopVerifyPoll]
  );

  const handleVerify = useCallback(
    async (emailId: number) => {
      stopVerifyPoll(emailId);
      setVerifyInFlight((prev) => {
        const next = new Set(prev);
        next.add(emailId);
        return next;
      });
      setVerifyErrors((prev) => {
        if (!(emailId in prev)) return prev;
        const next = { ...prev };
        delete next[emailId];
        return next;
      });

      let verifyRunId: number;
      try {
        const created = await verifyEmail(emailId);
        verifyRunId = created.verify_run_id;
      } catch (err) {
        finishVerify(emailId, undefined, errorMessage(err, "verification failed"));
        return;
      }

      const startedAt = Date.now();
      const handle = setInterval(async () => {
        try {
          const run = await getVerifyRun(verifyRunId);
          if (TERMINAL_STATUSES.has(run.status)) {
            const result = run.results.find((r) => r.email_id === emailId);
            const failure =
              run.status === "failed"
                ? run.error_message ?? "verification failed"
                : null;
            finishVerify(emailId, result, failure);
            return;
          }
          if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
            finishVerify(emailId, undefined, "verification timed out");
          }
        } catch (pollErr) {
          finishVerify(
            emailId,
            undefined,
            errorMessage(pollErr, "verification failed")
          );
        }
      }, POLL_INTERVAL_MS);
      verifyPollsRef.current.set(emailId, handle);
    },
    [finishVerify, stopVerifyPoll]
  );

  const startPhoneRevealPoll = useCallback(
    (emailId: number) => {
      // Apollo reveals the phone asynchronously (via webhook), so refetch the
      // scan on a short cadence until the number lands or the row is no longer
      // pending. Bounded by PHONE_REVEAL_TIMEOUT_MS so it never runs forever.
      stopEnrichPoll(emailId);
      const startedAt = Date.now();
      const handle = setInterval(async () => {
        if (Date.now() - startedAt > PHONE_REVEAL_TIMEOUT_MS) {
          stopEnrichPoll(emailId);
          return;
        }
        try {
          const next = await getScan(scanId);
          updateScan(next);
          const row = next.discovered_emails.find((r) => r.id === emailId);
          if (
            !row ||
            row.enriched_phone !== null ||
            !row.phone_reveal_pending
          ) {
            stopEnrichPoll(emailId);
          }
        } catch {
          stopEnrichPoll(emailId);
        }
      }, PHONE_REVEAL_POLL_MS);
      enrichPollsRef.current.set(emailId, handle);
    },
    [scanId, updateScan, stopEnrichPoll]
  );

  const handleEnrich = useCallback(
    async (emailId: number) => {
      setEnrichInFlight((prev) => {
        const next = new Set(prev);
        next.add(emailId);
        return next;
      });
      setEnrichErrors((prev) => {
        if (!(emailId in prev)) return prev;
        const next = { ...prev };
        delete next[emailId];
        return next;
      });

      try {
        const updated = await enrichEmail(emailId);
        setScan((prev) => {
          if (prev === null) return prev;
          const nextScan: ScanResponse = {
            ...prev,
            discovered_emails: prev.discovered_emails.map((row) =>
              row.id === emailId ? updated : row
            ),
          };
          onScanLoadedRef.current?.(nextScan);
          return nextScan;
        });
        if (updated.phone_reveal_pending) {
          startPhoneRevealPoll(emailId);
        }
      } catch (err) {
        setEnrichErrors((prev) => ({
          ...prev,
          [emailId]: enrichErrorMessage(err),
        }));
      } finally {
        setEnrichInFlight((prev) => {
          const next = new Set(prev);
          next.delete(emailId);
          return next;
        });
      }
    },
    [startPhoneRevealPoll]
  );

  // Resume phone-reveal polls on load/refetch — not just after a click.
  // `phone_reveal_pending` is a server-computed flag, so a freshly loaded scan
  // (e.g. a page reload, or landing on /master-list/{id}?scanId=…) can already
  // contain rows mid-reveal whose "finding phone…" spinner would otherwise sit
  // static with no poll driving it. Start a poll for every pending row, skipping
  // ids already polling (a manual Enrich, or a prior run of this effect) so we
  // never stack duplicate intervals on one id. Each poll self-terminates via its
  // own stop predicate when the number lands or the backend TTL clears the flag;
  // the unmount and scanId-change effects clear any still-running polls.
  useEffect(() => {
    if (!scan) return;
    for (const row of scan.discovered_emails) {
      if (row.phone_reveal_pending && !enrichPollsRef.current.has(row.id)) {
        startPhoneRevealPoll(row.id);
      }
    }
  }, [scan, startPhoneRevealPoll]);

  // Loading skeleton: pulse cards sized to roughly match the panels they
  // replace, so the layout doesn't jump when scan data lands.
  if (scan === null && loadError === null) {
    return (
      <div className="space-y-4">
        {showRunMetadata ? (
          <div className="h-40 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
        ) : null}
        <div className="h-64 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
      </div>
    );
  }

  if (scan === null) {
    return (
      <EmailExtractorErrorCard
        title="Couldn't load scan"
        message={loadError ?? "Unknown error."}
        onRetry={() => window.location.reload()}
      />
    );
  }

  const startedLabel = scan.started_at
    ? formatRelativeTime(scan.started_at)
    : "Not started";
  const completedLabel = scan.completed_at
    ? formatRelativeTime(scan.completed_at)
    : "—";

  // Page over the (possibly search-filtered) results. ``page`` can fall out of
  // range when the filtered set shrinks — e.g. the user types a query while on
  // a later page — so clamp before slicing rather than risk an empty render.
  const totalResults = filteredEmails.length;
  // Resolve "all" to the total row count (min 1 so an empty result set doesn't
  // divide by zero) — the slice then returns everything and the pager hides.
  const effectivePageSize =
    pageSize === "all" ? Math.max(1, totalResults) : pageSize;
  const totalPages = Math.max(1, Math.ceil(totalResults / effectivePageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageStart = safePage * effectivePageSize;
  const pagedEmails = filteredEmails.slice(
    pageStart,
    pageStart + effectivePageSize
  );
  const showPager = pageSize !== "all" && totalResults > effectivePageSize;

  return (
    <div className="space-y-4">
      {/* Status pill row */}
      <div className="flex flex-wrap items-center gap-2.5">
        <span
          aria-hidden
          className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT_CLASS[scan.status]}`}
        />
        <Pill variant={STATUS_PILL_VARIANT[scan.status]}>
          {STATUS_PILL_LABEL[scan.status]}
        </Pill>
        {isInFlight ? (
          <span className="inline-flex items-center gap-1.5 text-[12px] text-[var(--text-muted,#94a3b8)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            polling for updates…
          </span>
        ) : null}
        {scan.error_message ? (
          <span className="text-[12px] text-[var(--pill-red-text,#b91c1c)]">
            {scan.error_message}
          </span>
        ) : null}
      </div>

      {timedOut ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-[12px] text-[var(--pill-amber-text,#b45309)]">
          <AlertCircle
            className="mt-0.5 h-4 w-4 flex-shrink-0"
            strokeWidth={2}
          />
          <span>
            Still running after 3 minutes. Refresh the page to resume polling.
          </span>
        </div>
      ) : null}

      {loadError !== null ? (
        <div className="flex items-start gap-2 rounded-md border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle
            className="mt-0.5 h-4 w-4 flex-shrink-0"
            strokeWidth={2}
          />
          <span>{loadError}</span>
        </div>
      ) : null}

      {showRunMetadata ? (
        <SectionPanel eyebrow="Run metadata" title="Scan details">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <MiniStat label="Domain" value={scan.domain} />
            <MiniStat
              label="Person"
              value={scan.person_name ?? "—"}
              helper={scan.person_name ? undefined : "Not specified"}
            />
            <MiniStat label="Pipeline" value={scan.pipeline_name} />
            <MiniStat
              label="Processed"
              value={
                scan.total_items > 0
                  ? `${scan.processed_items} / ${scan.total_items}`
                  : "—"
              }
              valueTitle={
                scan.total_items > 0 ? undefined : "Scan hasn't started"
              }
            />
            <MiniStat
              label="Success / Failure"
              value={`${scan.success_count} / ${scan.failure_count}`}
            />
            <MiniStat
              label="Created"
              value={formatDate(scan.created_at)}
              helper={formatRelativeTime(scan.created_at)}
            />
            <MiniStat
              label="Started"
              value={scan.started_at ? formatDate(scan.started_at) : "—"}
              helper={scan.started_at ? startedLabel : undefined}
              valueTitle={scan.started_at ? undefined : "Scan still queued"}
            />
            <MiniStat
              label="Completed"
              value={scan.completed_at ? formatDate(scan.completed_at) : "—"}
              helper={scan.completed_at ? completedLabel : undefined}
              valueTitle={
                scan.completed_at ? undefined : "Scan still in progress"
              }
            />
          </div>
        </SectionPanel>
      ) : null}

      <SectionPanel
        eyebrow="Discovered emails"
        title="Results"
        headerAction={
          <EnrichAllButton
            scanId={scan.id}
            unenrichedCount={unenrichedCount}
            onProgress={() => void refetchScan()}
          />
        }
      >
        {scan.status === "failed" ? (
          <EmailExtractorErrorCard
            title="Scan failed"
            message={dynamicScanErrorSubtext(scan.error_message)}
            onRetry={() => window.location.reload()}
            retryLabel="Reload"
          />
        ) : scan.discovered_emails.length === 0 &&
          (scan.status === "queued" || scan.status === "running") ? (
          <ScanResultsLoading
            processed={scan.processed_items}
            total={scan.total_items}
          />
        ) : scan.discovered_emails.length === 0 ? (
          <EmptyScanResultsState />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:max-w-sm">
                <Search
                  className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted,#94a3b8)]"
                  strokeWidth={2}
                />
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(e) => {
                    setSearchQuery(e.target.value);
                    setPage(0);
                  }}
                  placeholder="Search email, name, title, company, or phone"
                  aria-label="Search discovered emails"
                  className="w-full rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1.5 pl-8 pr-3 text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:border-[var(--blue,#3b82f6)] focus:outline-none focus:ring-2 focus:ring-[rgba(59,130,246,0.2)]"
                />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                {searchQuery.trim() !== "" ? (
                  <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
                    Showing {filteredEmails.length} of{" "}
                    {scan.discovered_emails.length}
                  </p>
                ) : null}
                <RowsPerPageSelect
                  value={pageSize}
                  onChange={(next) => {
                    setPageSize(next);
                    setPage(0);
                    writeStoredPageSize(next);
                  }}
                />
              </div>
            </div>
            {filteredEmails.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
                No emails match &ldquo;{searchQuery}&rdquo;.
              </div>
            ) : (
              <>
                <ResultsTable
                  rows={pagedEmails}
                  localVerifications={localVerifications}
                  verifyInFlight={verifyInFlight}
                  verifyErrors={verifyErrors}
                  onVerify={handleVerify}
                  enrichInFlight={enrichInFlight}
                  enrichErrors={enrichErrors}
                  onEnrich={handleEnrich}
                  savedContactIds={savedContactIds}
                  onSavedChange={handleSavedChange}
                />
                {showPager ? (
                  <ResultsPager
                    page={safePage}
                    pageSize={effectivePageSize}
                    total={totalResults}
                    onPrev={() => setPage(Math.max(0, safePage - 1))}
                    onNext={() =>
                      setPage(Math.min(totalPages - 1, safePage + 1))
                    }
                  />
                ) : null}
              </>
            )}
          </div>
        )}
      </SectionPanel>
    </div>
  );
}
