"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import {
  Building2,
  ChevronDown,
  ChevronRight,
  ListChecks,
  Loader2,
  Mail,
  Search,
  Send,
  X,
} from "lucide-react";

import {
  ApiError,
  listFavoriteFirms,
  listFirmContacts,
  searchOutreachContacts,
  searchOutreachFavorites,
  searchOutreachFirms,
} from "@/lib/api";
import type {
  FavoriteSearchResult,
  FirmSearchResult,
  RecipientSearchResult,
} from "@/lib/types";

// Compose-tab recipient picker. The autocomplete renders three kinds of
// hit:
//
//   1. Contacts -- direct pick (no modal); recipient is set to that
//      contact + firm context.
//   2. Firms -- picking opens a "pick a contact at this firm" modal;
//      the modal's pick wires the recipient with that contact.
//   3. Favorite lists -- picking opens a "pick a firm in this list"
//      modal; the user's pick there transitions to the firm-contacts
//      modal for the chosen firm, then picking a contact wires the
//      recipient.
//
// Plus the free-form-email path: when the typed query parses as an
// email and no contact matches, the dropdown surfaces a synthetic
// "send to <typed> as a one-off" row that bypasses the firm/favorite
// drill-downs and sets recipient.kind="adhoc".

export type RecipientValue =
  | { kind: "contact"; result: RecipientSearchResult }
  | { kind: "adhoc"; email: string; name?: string | null };

interface RecipientPickerProps {
  value: RecipientValue | null;
  onChange: (value: RecipientValue | null) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

const EMAIL_REGEX = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i;
const SEARCH_DEBOUNCE_MS = 250;
const ENTITY_KIND_LABEL: Record<
  RecipientSearchResult["entity_kind"],
  string
> = {
  broker_dealer: "Broker-dealer",
  advisor: "Advisor",
  institutional_investor: "Investor",
};

type DropdownOption =
  | { kind: "firm"; item: FirmSearchResult }
  | { kind: "favorite"; item: FavoriteSearchResult }
  | { kind: "contact"; item: RecipientSearchResult }
  | { kind: "adhoc"; email: string };

export function RecipientPicker({
  value,
  onChange,
  disabled = false,
  ariaLabel,
}: RecipientPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [contacts, setContacts] = useState<RecipientSearchResult[]>([]);
  const [firms, setFirms] = useState<FirmSearchResult[]>([]);
  const [favorites, setFavorites] = useState<FavoriteSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);

  // Drill-down modal state. firmModal is set when the user picks a
  // firm (directly OR through a favorite-list step). favoriteModal is
  // set when the user picks a favorite list. They're mutually exclusive
  // -- selecting a firm from inside the favorite modal closes the
  // favorite modal and opens the firm one.
  const [firmModal, setFirmModal] = useState<{
    entity_kind: FirmSearchResult["entity_kind"];
    entity_id: number;
    entity_name: string;
  } | null>(null);
  const [favoriteModal, setFavoriteModal] = useState<{
    list_id: number;
    name: string;
  } | null>(null);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    function onDocumentMouseDown(event: globalThis.MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentMouseDown);
    return () => document.removeEventListener("mousedown", onDocumentMouseDown);
  }, []);

  // Debounced parallel fetch on query change. Three endpoints; merged
  // into three section arrays. One global loading flag so the dropdown
  // shows "Searching…" until the slowest call returns.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setContacts([]);
      setFirms([]);
      setFavorites([]);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    const handle = window.setTimeout(async () => {
      try {
        const [contactsResp, firmsResp, favoritesResp] = await Promise.all([
          searchOutreachContacts(trimmed),
          searchOutreachFirms(trimmed),
          searchOutreachFavorites(trimmed),
        ]);
        if (!isMountedRef.current) return;
        setContacts(contactsResp.items);
        setFirms(firmsResp.items);
        setFavorites(favoritesResp.items);
      } catch (err) {
        if (!isMountedRef.current) return;
        setContacts([]);
        setFirms([]);
        setFavorites([]);
        setError(
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Search failed.",
        );
      } finally {
        if (isMountedRef.current) setLoading(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query]);

  const trimmed = query.trim();
  const isEmailQuery = EMAIL_REGEX.test(trimmed);
  const adhocAvailable =
    isEmailQuery &&
    !contacts.some(
      (r) => r.contact_email.toLowerCase() === trimmed.toLowerCase(),
    );

  // Flat option list in display order. Keyboard nav walks it linearly;
  // the rendered dropdown re-derives section headers from option.kind.
  const options: DropdownOption[] = [
    ...firms.map((item) => ({ kind: "firm" as const, item })),
    ...favorites.map((item) => ({ kind: "favorite" as const, item })),
    ...contacts.map((item) => ({ kind: "contact" as const, item })),
    ...(adhocAvailable
      ? [{ kind: "adhoc" as const, email: trimmed }]
      : []),
  ];

  function pickContact(result: RecipientSearchResult) {
    onChange({ kind: "contact", result });
    setQuery("");
    setOpen(false);
    setActiveIdx(0);
  }

  function pickAdhoc(email: string) {
    onChange({ kind: "adhoc", email });
    setQuery("");
    setOpen(false);
    setActiveIdx(0);
  }

  function pickFirm(firm: FirmSearchResult) {
    // Open the firm-contacts modal directly. The user's contact pick
    // inside the modal will fire onChange via this picker's
    // pickContact handler.
    setFirmModal({
      entity_kind: firm.entity_kind,
      entity_id: firm.entity_id,
      entity_name: firm.entity_name,
    });
    setOpen(false);
  }

  function pickFavorite(fav: FavoriteSearchResult) {
    setFavoriteModal({ list_id: fav.list_id, name: fav.name });
    setOpen(false);
  }

  function commitOption(opt: DropdownOption) {
    if (opt.kind === "contact") pickContact(opt.item);
    else if (opt.kind === "adhoc") pickAdhoc(opt.email);
    else if (opt.kind === "firm") pickFirm(opt.item);
    else pickFavorite(opt.item);
  }

  function clear() {
    onChange(null);
    setQuery("");
    inputRef.current?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      if (options.length > 0) setActiveIdx((i) => (i + 1) % options.length);
    } else if (event.key === "ArrowUp") {
      if (options.length === 0) return;
      event.preventDefault();
      setActiveIdx((i) => (i - 1 + options.length) % options.length);
    } else if (event.key === "Enter") {
      if (options.length === 0) {
        if (isEmailQuery) {
          event.preventDefault();
          pickAdhoc(trimmed);
        }
        return;
      }
      event.preventDefault();
      commitOption(options[activeIdx]);
    } else if (event.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  }

  // ── Drill-down modals ────────────────────────────────────────────
  function closeAllModals() {
    setFirmModal(null);
    setFavoriteModal(null);
  }

  const modal = firmModal ? (
    <FirmContactsDialog
      entityKind={firmModal.entity_kind}
      entityId={firmModal.entity_id}
      entityName={firmModal.entity_name}
      onPick={(contact) => {
        closeAllModals();
        pickContact(contact);
      }}
      onClose={closeAllModals}
    />
  ) : favoriteModal ? (
    <FavoriteFirmsDialog
      listId={favoriteModal.list_id}
      name={favoriteModal.name}
      onPickFirm={(firm) => {
        setFavoriteModal(null);
        pickFirm(firm);
      }}
      onClose={closeAllModals}
    />
  ) : null;

  if (value) {
    return (
      <>
        <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3.5 py-2">
          <RecipientChip value={value} />
          <button
            type="button"
            onClick={clear}
            disabled={disabled}
            aria-label="Clear recipient"
            className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-full text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--red,#ef4444)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        </div>
        {modal}
      </>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <div
        onClick={() => {
          if (disabled) return;
          setOpen(true);
          inputRef.current?.focus();
        }}
        className={`relative flex items-center gap-2 rounded-[10px] border bg-[var(--surface,#ffffff)] px-3.5 py-2 transition ${
          disabled ? "opacity-60" : ""
        } ${
          open
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        <Search
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
            setActiveIdx(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder="Search by name, firm, favorite list, or type an email…"
          aria-label={ariaLabel ?? "Recipient"}
          autoComplete="off"
          className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
        />
        <ChevronDown
          className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)]"
          strokeWidth={2}
        />
      </div>

      {open && trimmed ? (
        <div
          role="listbox"
          className="absolute left-0 right-0 top-full z-10 mt-1 max-h-80 overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] py-1 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]"
        >
          {loading && options.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              Searching…
            </div>
          ) : null}

          {!loading && error ? (
            <div className="px-3 py-2 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
              {error}
            </div>
          ) : null}

          {!loading && !error && options.length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              No matches. Type a full email address to send a one-off.
            </div>
          ) : null}

          <DropdownSections
            options={options}
            activeIdx={activeIdx}
            onHover={setActiveIdx}
            onPick={commitOption}
          />
        </div>
      ) : null}

      {modal}
    </div>
  );
}

function DropdownSections({
  options,
  activeIdx,
  onHover,
  onPick,
}: {
  options: DropdownOption[];
  activeIdx: number;
  onHover: (idx: number) => void;
  onPick: (opt: DropdownOption) => void;
}) {
  type SectionKey = "firm" | "favorite" | "contact" | "adhoc";
  const sections: {
    key: SectionKey;
    label: string;
    entries: { idx: number; opt: DropdownOption }[];
  }[] = [
    { key: "firm", label: "Firms", entries: [] },
    { key: "favorite", label: "Favorite lists", entries: [] },
    { key: "contact", label: "Contacts", entries: [] },
    { key: "adhoc", label: "One-off send", entries: [] },
  ];
  options.forEach((opt, idx) => {
    const target = sections.find((s) => s.key === opt.kind);
    if (target) target.entries.push({ idx, opt });
  });
  return (
    <>
      {sections.map((section) =>
        section.entries.length === 0 ? null : (
          <div key={section.key}>
            <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
              {section.label}
            </div>
            {section.entries.map(({ idx, opt }) => (
              <DropdownRow
                key={`${opt.kind}-${idx}`}
                opt={opt}
                active={idx === activeIdx}
                onHover={() => onHover(idx)}
                onPick={() => onPick(opt)}
              />
            ))}
          </div>
        ),
      )}
    </>
  );
}

function DropdownRow({
  opt,
  active,
  onHover,
  onPick,
}: {
  opt: DropdownOption;
  active: boolean;
  onHover: () => void;
  onPick: () => void;
}) {
  const baseClass = `block w-full cursor-pointer px-3 py-2 text-left text-[13px] transition ${
    active ? "bg-[var(--surface-2,#f1f6fd)]" : "bg-transparent"
  }`;
  const onMouseDown = (event: React.MouseEvent) => {
    event.preventDefault();
    onPick();
  };

  if (opt.kind === "firm") {
    const f = opt.item;
    return (
      <button
        type="button"
        role="option"
        aria-selected={active}
        onMouseDown={onMouseDown}
        onMouseEnter={onHover}
        className={baseClass}
      >
        <div className="flex items-center gap-2">
          <Building2 className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted,#94a3b8)]" />
          <span className="flex-1 truncate font-semibold text-[var(--text,#0f172a)]">
            {f.entity_name}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
            {ENTITY_KIND_LABEL[f.entity_kind]}
          </span>
          <span className="text-[11px] text-[var(--text-dim,#475569)]">
            {f.contact_count} contact{f.contact_count === 1 ? "" : "s"}
          </span>
          <ChevronRight className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" />
        </div>
      </button>
    );
  }
  if (opt.kind === "favorite") {
    const fav = opt.item;
    return (
      <button
        type="button"
        role="option"
        aria-selected={active}
        onMouseDown={onMouseDown}
        onMouseEnter={onHover}
        className={baseClass}
      >
        <div className="flex items-center gap-2">
          <ListChecks className="h-3.5 w-3.5 shrink-0 text-[var(--accent,#6366f1)]" />
          <span className="flex-1 truncate font-semibold text-[var(--text,#0f172a)]">
            {fav.name}
          </span>
          <span className="text-[11px] text-[var(--text-dim,#475569)]">
            {fav.firm_count} firm{fav.firm_count === 1 ? "" : "s"}
          </span>
          <ChevronRight className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" />
        </div>
      </button>
    );
  }
  if (opt.kind === "contact") {
    const c = opt.item;
    return (
      <button
        type="button"
        role="option"
        aria-selected={active}
        onMouseDown={onMouseDown}
        onMouseEnter={onHover}
        className={baseClass}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-semibold text-[var(--text,#0f172a)]">
            {c.contact_name || c.contact_email}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
            {ENTITY_KIND_LABEL[c.entity_kind]}
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-dim,#475569)]">
          <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" />
          <span className="truncate">{c.contact_email}</span>
          <span className="text-[var(--text-muted,#94a3b8)]">·</span>
          <span className="truncate">{c.entity_name}</span>
          {c.contact_title ? (
            <>
              <span className="text-[var(--text-muted,#94a3b8)]">·</span>
              <span className="truncate">{c.contact_title}</span>
            </>
          ) : null}
        </div>
      </button>
    );
  }
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      onMouseDown={onMouseDown}
      onMouseEnter={onHover}
      className={baseClass}
    >
      <div className="flex items-center gap-2">
        <Send className="h-3.5 w-3.5 text-[var(--accent,#6366f1)]" />
        <span className="font-semibold text-[var(--text,#0f172a)]">
          Send to{" "}
          <span className="text-[var(--accent,#6366f1)]">{opt.email}</span>
        </span>
        <span className="ml-auto inline-flex rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
          One-off
        </span>
      </div>
      <div className="mt-0.5 text-[11px] text-[var(--text-dim,#475569)]">
        One-off send. No firm / contact will be linked.
      </div>
    </button>
  );
}

function RecipientChip({ value }: { value: RecipientValue }) {
  if (value.kind === "adhoc") {
    return (
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Send className="h-4 w-4 shrink-0 text-[var(--accent,#6366f1)]" />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">
            {value.email}
          </span>
          <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
            One-off · no firm linked
          </span>
        </div>
      </div>
    );
  }
  const { result } = value;
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <Mail className="h-4 w-4 shrink-0 text-[var(--accent,#6366f1)]" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">
          {result.contact_name || result.contact_email}
        </span>
        <span className="truncate text-[11px] text-[var(--text-dim,#475569)]">
          {result.contact_email} · {result.entity_name}
          {result.contact_title ? ` · ${result.contact_title}` : ""}
        </span>
      </div>
      <span className="inline-flex shrink-0 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
        {ENTITY_KIND_LABEL[result.entity_kind]}
      </span>
    </div>
  );
}

// ── Modals ────────────────────────────────────────────────────────────

function FirmContactsDialog({
  entityKind,
  entityId,
  entityName,
  onPick,
  onClose,
}: {
  entityKind: FirmSearchResult["entity_kind"];
  entityId: number;
  entityName: string;
  onPick: (contact: RecipientSearchResult) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<RecipientSearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const resp = await listFirmContacts(entityKind, entityId);
        if (!isMountedRef.current) return;
        setItems(resp.items);
      } catch (err) {
        if (!isMountedRef.current) return;
        setError(
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Could not load contacts.",
        );
      }
    })();
  }, [entityKind, entityId]);

  return (
    <ModalShell
      title="Pick a contact"
      subtitle={`${entityName} · ${ENTITY_KIND_LABEL[entityKind]}`}
      onClose={onClose}
    >
      {items === null && !error ? (
        <DialogLoading label="Loading contacts…" />
      ) : null}
      {error ? <DialogError message={error} /> : null}
      {items && items.length === 0 ? (
        <p className="px-1 py-6 text-center text-[12px] text-[var(--text-muted,#94a3b8)]">
          No email-bearing contacts at this firm.
        </p>
      ) : null}
      {items && items.length > 0 ? (
        <ul className="max-h-[60vh] divide-y divide-[var(--border,rgba(30,64,175,0.1))] overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))]">
          {items.map((contact) => (
            <li key={contact.contact_id}>
              <button
                type="button"
                onClick={() => onPick(contact)}
                className="block w-full px-3 py-2 text-left text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)]"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-semibold text-[var(--text,#0f172a)]">
                    {contact.contact_name || contact.contact_email}
                  </span>
                  {contact.contact_title ? (
                    <span className="text-[11px] text-[var(--text-dim,#475569)]">
                      {contact.contact_title}
                    </span>
                  ) : null}
                </div>
                <div className="mt-0.5 flex items-center gap-1 text-[11px] text-[var(--text-dim,#475569)]">
                  <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" />
                  <span className="truncate">{contact.contact_email}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </ModalShell>
  );
}

function FavoriteFirmsDialog({
  listId,
  name,
  onPickFirm,
  onClose,
}: {
  listId: number;
  name: string;
  onPickFirm: (firm: FirmSearchResult) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<FirmSearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const resp = await listFavoriteFirms(listId);
        if (!isMountedRef.current) return;
        setItems(resp.items);
      } catch (err) {
        if (!isMountedRef.current) return;
        setError(
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Could not load firms.",
        );
      }
    })();
  }, [listId]);

  return (
    <ModalShell
      title="Pick a firm in this list"
      subtitle={name}
      onClose={onClose}
    >
      {items === null && !error ? (
        <DialogLoading label="Loading firms…" />
      ) : null}
      {error ? <DialogError message={error} /> : null}
      {items && items.length === 0 ? (
        <p className="px-1 py-6 text-center text-[12px] text-[var(--text-muted,#94a3b8)]">
          No email-bearing-contact firms in this list.
        </p>
      ) : null}
      {items && items.length > 0 ? (
        <ul className="max-h-[60vh] divide-y divide-[var(--border,rgba(30,64,175,0.1))] overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))]">
          {items.map((firm) => (
            <li key={`${firm.entity_kind}-${firm.entity_id}`}>
              <button
                type="button"
                onClick={() => onPickFirm(firm)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition hover:bg-[var(--surface-2,#f1f6fd)]"
              >
                <Building2 className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted,#94a3b8)]" />
                <span className="flex-1 truncate font-semibold text-[var(--text,#0f172a)]">
                  {firm.entity_name}
                </span>
                <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
                  {ENTITY_KIND_LABEL[firm.entity_kind]}
                </span>
                <span className="text-[11px] text-[var(--text-dim,#475569)]">
                  {firm.contact_count} contact{firm.contact_count === 1 ? "" : "s"}
                </span>
                <ChevronRight className="h-3.5 w-3.5 text-[var(--text-muted,#94a3b8)]" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </ModalShell>
  );
}

function ModalShell({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={onClose}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[520px] rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
              {title}
            </p>
            <h2 className="mt-1 truncate text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              {subtitle}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-1 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text-dim,#475569)]"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

function DialogLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-8 text-[12px] text-[var(--text-muted,#94a3b8)]">
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

function DialogError({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-[12px] text-[var(--pill-red-text,#b91c1c)]">
      {message}
    </div>
  );
}
