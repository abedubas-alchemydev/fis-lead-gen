"use client";

import { AlertTriangle, Bookmark, ExternalLink, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  RowsPerPageSelect,
  type RowsPerPageValue,
} from "@/components/email-extractor/rows-per-page-select";
import { Button } from "@/components/ui/button";
import { SectionPanel } from "@/components/ui/section-panel";
import { useToast } from "@/components/ui/use-toast";
import { deleteSavedContact, listSavedContacts } from "@/lib/saved-contacts";
import type { SavedContact } from "@/types/saved-contact";

// Client for /saved-contacts. Fetches the user's saved contacts (all sources)
// and renders a searchable, client-paged table with a per-row Remove. Layout
// mirrors the simple-list surfaces (/my-favorites, /visited-firms): a live
// count strip above a SectionPanel. The search + rows-per-page controls and
// the results table reuse the email-extractor styling so the two surfaces read
// as a matched pair (the RowsPerPageSelect is literally shared).

const DEFAULT_PAGE_SIZE = 100;

// Case-insensitive match across every displayed column.
function matchesQuery(contact: SavedContact, q: string): boolean {
  const fields = [
    contact.name,
    contact.title,
    contact.email,
    contact.company,
    contact.phone,
    contact.linkedin_url,
  ];
  return fields.some(
    (field) => field !== null && field.toLowerCase().includes(q)
  );
}

export function SavedContactsClient(): React.ReactElement {
  const toast = useToast();
  const [contacts, setContacts] = useState<SavedContact[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<RowsPerPageValue>(DEFAULT_PAGE_SIZE);
  // Per-row in-flight removals — disables the row's button + shows "Removing…".
  const [removing, setRemoving] = useState<Set<number>>(new Set());

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(null);
    listSavedContacts()
      .then((response) => {
        if (!active) return;
        setContacts(response);
      })
      .catch((err) => {
        if (!active) return;
        setLoadError(
          err instanceof Error ? err.message : "Couldn't load saved contacts."
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return contacts;
    return contacts.filter((contact) => matchesQuery(contact, q));
  }, [contacts, searchQuery]);

  // Client-side paging — identical resolution to the email-extractor table:
  // "all" collapses to the row count so the slice returns everything.
  const totalResults = filtered.length;
  const effectivePageSize =
    pageSize === "all" ? Math.max(1, totalResults) : pageSize;
  const totalPages = Math.max(1, Math.ceil(totalResults / effectivePageSize));
  const safePage = Math.min(page, totalPages - 1);
  const pageStart = safePage * effectivePageSize;
  const paged = filtered.slice(pageStart, pageStart + effectivePageSize);
  const showPager = pageSize !== "all" && totalResults > effectivePageSize;

  async function handleRemove(contact: SavedContact) {
    if (removing.has(contact.id)) return;
    setRemoving((prev) => {
      const next = new Set(prev);
      next.add(contact.id);
      return next;
    });
    // Optimistic removal — snapshot first so we can roll back on failure.
    const snapshot = contacts;
    setContacts((prev) => prev.filter((c) => c.id !== contact.id));
    try {
      await deleteSavedContact(contact.id);
      toast.success(
        `Removed ${contact.name ?? contact.email ?? "contact"} from saved.`
      );
    } catch (err) {
      setContacts(snapshot);
      toast.error(
        err instanceof Error ? err.message : "Couldn't remove contact."
      );
    } finally {
      setRemoving((prev) => {
        const next = new Set(prev);
        next.delete(contact.id);
        return next;
      });
    }
  }

  return (
    <>
      {/* Live count strip — mirrors /visited-firms + /my-favorites. */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {loading
            ? "Loading…"
            : `${contacts.length.toLocaleString()} saved contact${
                contacts.length === 1 ? "" : "s"
              }`}
        </span>
      </div>

      <SectionPanel eyebrow="Personal" title="Saved contacts">
        {loading ? (
          <TableSkeleton />
        ) : loadError ? (
          <LoadErrorCard
            message={loadError}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        ) : contacts.length === 0 ? (
          <EmptySavedState />
        ) : (
          <div className="flex flex-col gap-3">
            {/* Controls row — search + rows-per-page, mirroring scan-detail-view. */}
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
                  placeholder="Search name, title, email, company, phone, or LinkedIn"
                  aria-label="Search saved contacts"
                  className="w-full rounded-md border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1.5 pl-8 pr-3 text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:border-[var(--blue,#3b82f6)] focus:outline-none focus:ring-2 focus:ring-[rgba(59,130,246,0.2)]"
                />
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                {searchQuery.trim() !== "" ? (
                  <p className="text-[12px] text-[var(--text-muted,#94a3b8)]">
                    Showing {filtered.length} of {contacts.length}
                  </p>
                ) : null}
                <RowsPerPageSelect
                  value={pageSize}
                  onChange={(next) => {
                    setPageSize(next);
                    setPage(0);
                  }}
                />
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-4 py-6 text-center text-[13px] text-[var(--text-muted,#94a3b8)]">
                No contacts match &ldquo;{searchQuery}&rdquo;.
              </div>
            ) : (
              <>
                <SavedContactsTable
                  rows={paged}
                  removing={removing}
                  onRemove={(contact) => void handleRemove(contact)}
                />
                {showPager ? (
                  <Pager
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
    </>
  );
}

function Dash(): React.ReactElement {
  return <span className="text-[var(--text-muted,#94a3b8)]">—</span>;
}

function SavedContactsTable({
  rows,
  removing,
  onRemove,
}: {
  rows: SavedContact[];
  removing: Set<number>;
  onRemove: (contact: SavedContact) => void;
}): React.ReactElement {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]">
      <table className="w-full text-[13px]">
        <thead className="bg-[var(--surface-2,#f1f6fd)] text-left">
          <tr>
            {["Name", "Title", "Email", "Company", "Phone", "LinkedIn"].map(
              (label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]"
                >
                  {label}
                </th>
              )
            )}
            <th scope="col" className="px-4 py-2.5">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
          {rows.map((contact) => (
            <tr key={contact.id} className="align-top">
              <td className="px-4 py-3 font-semibold text-[var(--text,#0f172a)]">
                {contact.name ?? <Dash />}
              </td>
              <td className="px-4 py-3 text-[var(--text-dim,#475569)]">
                {contact.title ?? <Dash />}
              </td>
              <td className="px-4 py-3 font-mono text-[12px] text-[var(--text,#0f172a)]">
                {contact.email ? (
                  <a
                    href={`mailto:${contact.email}`}
                    className="text-[var(--accent,#6366f1)] transition hover:underline"
                  >
                    {contact.email}
                  </a>
                ) : (
                  <Dash />
                )}
              </td>
              <td className="px-4 py-3 text-[var(--text-dim,#475569)]">
                {contact.company ?? <Dash />}
              </td>
              <td className="px-4 py-3 tabular-nums text-[var(--text-dim,#475569)]">
                {contact.phone ? (
                  <a
                    href={`tel:${contact.phone}`}
                    className="transition hover:underline"
                  >
                    {contact.phone}
                  </a>
                ) : (
                  <Dash />
                )}
              </td>
              <td className="px-4 py-3">
                {contact.linkedin_url ? (
                  <a
                    href={contact.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[var(--accent,#6366f1)] transition hover:underline"
                  >
                    <ExternalLink
                      className="h-3.5 w-3.5"
                      strokeWidth={2}
                      aria-hidden
                    />
                    Profile
                  </a>
                ) : (
                  <Dash />
                )}
              </td>
              <td className="px-4 py-3 text-right">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onRemove(contact)}
                  disabled={removing.has(contact.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
                  {removing.has(contact.id) ? "Removing…" : "Remove"}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Prev / range / Next pager — same affordance as the email-extractor
// ResultsPager so the two tables page identically.
function Pager({
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
        aria-label="Previous page of saved contacts"
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
        aria-label="Next page of saved contacts"
      >
        Next
      </button>
    </div>
  );
}

function TableSkeleton(): React.ReactElement {
  return (
    <div className="space-y-2" aria-busy>
      {Array.from({ length: 6 }).map((_, index) => (
        <div
          key={`saved-skel-${index}`}
          className="h-[52px] animate-pulse rounded-lg bg-[var(--surface-2,#f1f6fd)]"
        />
      ))}
    </div>
  );
}

function EmptySavedState(): React.ReactElement {
  return (
    <div className="my-2 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]">
        <Bookmark className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        No saved contacts yet
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Save a contact from the email extractor results to keep it here for
        quick access.
      </p>
    </div>
  );
}

// Initial-fetch failure card — dashed border + Retry, mirroring the
// LoadErrorCard shipped on /visited-firms.
function LoadErrorCard({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div className="my-4 rounded-2xl border border-dashed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-6 py-12 text-center">
      <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-[rgba(239,68,68,0.1)] text-[var(--pill-red-text,#b91c1c)]">
        <AlertTriangle className="h-6 w-6" strokeWidth={1.75} aria-hidden />
      </div>
      <h3 className="mt-5 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
        Couldn&apos;t load your saved contacts
      </h3>
      <p className="mx-auto mt-2 max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        {message}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry} className="mt-5">
        Retry
      </Button>
    </div>
  );
}
