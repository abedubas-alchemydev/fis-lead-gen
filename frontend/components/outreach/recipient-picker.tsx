"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import {
  Building2,
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

// Compose-tab recipient picker. MULTI-SELECT: the value is a list of
// recipients rendered as removable chips, and each send goes out as its
// own individual 1:1 email (the orchestration lives in
// create-outreach-tab.tsx). The autocomplete renders four kinds of hit:
//
//   1. Contacts -- direct pick adds that contact as a chip.
//   2. Firms -- picking opens a "pick contacts at this firm" modal with
//      checkboxes; the modal's "Add" wires every checked contact.
//   3. Favorite lists -- picking opens a "pick a firm in this list"
//      modal; the user's pick there transitions to the firm-contacts
//      modal for the chosen firm, then checking contacts wires them.
//   4. Free-form email -- when the typed query parses as an email and no
//      contact matches, the dropdown surfaces a synthetic "send to
//      <typed> as a one-off" row that adds an ``adhoc`` chip.
//
// Recipients are de-duplicated by lower-cased email, so the same address
// can never be added twice (whether via contact, firm, or one-off).

export type RecipientValue =
  | { kind: "contact"; result: RecipientSearchResult }
  | { kind: "adhoc"; email: string; name?: string | null };

// Email of a recipient regardless of kind. Exported so the send
// orchestrator can label per-recipient results.
export function recipientEmail(value: RecipientValue): string {
  return value.kind === "adhoc" ? value.email : value.result.contact_email;
}

// Stable identity for de-dup + React keys. Two recipients with the same
// address (case-insensitive) collapse to one.
export function recipientKey(value: RecipientValue): string {
  return recipientEmail(value).toLowerCase();
}

function recipientLabel(value: RecipientValue): string {
  if (value.kind === "adhoc") return value.name || value.email;
  return value.result.contact_name || value.result.contact_email;
}

interface RecipientPickerProps {
  value: RecipientValue[];
  onChange: (value: RecipientValue[]) => void;
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

const PRIMARY_BTN =
  "inline-flex items-center justify-center gap-1.5 rounded-[10px] bg-[var(--accent,#6366f1)] px-3.5 py-2 text-[13px] font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40";
const OUTLINE_BTN =
  "inline-flex items-center justify-center rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] px-3.5 py-2 text-[13px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]";

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

  // Already-picked addresses (lower-cased). Drives de-dup on add and the
  // "Added" markers in the dropdown + firm-contacts modal.
  const existingKeys = useMemo(
    () => new Set(value.map(recipientKey)),
    [value],
  );

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
    !existingKeys.has(trimmed.toLowerCase()) &&
    !contacts.some(
      (r) => r.contact_email.toLowerCase() === trimmed.toLowerCase(),
    );

  // Flat option list in display order. Keyboard nav walks it linearly;
  // the rendered dropdown re-derives section headers from option.kind.
  const options: DropdownOption[] = [
    ...firms.map((item) => ({ kind: "firm" as const, item })),
    ...favorites.map((item) => ({ kind: "favorite" as const, item })),
    ...contacts.map((item) => ({ kind: "contact" as const, item })),
    ...(adhocAvailable ? [{ kind: "adhoc" as const, email: trimmed }] : []),
  ];

  // ── Recipient list mutation (de-duped by email) ──────────────────
  function addOne(recipient: RecipientValue) {
    const key = recipientKey(recipient);
    if (value.some((v) => recipientKey(v) === key)) return;
    onChange([...value, recipient]);
  }

  function addMany(recipients: RecipientValue[]) {
    const seen = new Set(value.map(recipientKey));
    const next = [...value];
    for (const recipient of recipients) {
      const key = recipientKey(recipient);
      if (seen.has(key)) continue;
      seen.add(key);
      next.push(recipient);
    }
    if (next.length !== value.length) onChange(next);
  }

  function removeKey(key: string) {
    onChange(value.filter((v) => recipientKey(v) !== key));
  }

  // Adding a contact / one-off keeps the picker focused and clears the
  // query so the user can immediately search for the next recipient.
  function pickContact(result: RecipientSearchResult) {
    addOne({ kind: "contact", result });
    setQuery("");
    setActiveIdx(0);
    inputRef.current?.focus();
  }

  function pickAdhoc(email: string) {
    addOne({ kind: "adhoc", email });
    setQuery("");
    setActiveIdx(0);
    inputRef.current?.focus();
  }

  function pickFirm(firm: FirmSearchResult) {
    // Open the firm-contacts modal. The user's contact checkboxes inside
    // the modal commit via this picker's addMany on "Add".
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

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace" && query === "" && value.length > 0) {
      event.preventDefault();
      removeKey(recipientKey(value[value.length - 1]));
      return;
    }
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
      existingKeys={existingKeys}
      onAdd={(picked) => {
        closeAllModals();
        addMany(picked.map((result) => ({ kind: "contact", result })));
        inputRef.current?.focus();
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

  return (
    <div ref={rootRef} className="relative">
      <div
        onClick={() => {
          if (disabled) return;
          setOpen(true);
          inputRef.current?.focus();
        }}
        className={`flex flex-wrap items-center gap-1.5 rounded-[10px] border bg-[var(--surface,#ffffff)] px-3 py-2 transition ${
          disabled ? "opacity-60" : ""
        } ${
          open
            ? "border-[var(--accent,#6366f1)] shadow-[0_0_0_3px_rgba(99,102,241,0.15)]"
            : "border-[var(--border,rgba(30,64,175,0.1))]"
        }`}
      >
        {value.map((recipient) => (
          <RecipientChip
            key={recipientKey(recipient)}
            value={recipient}
            disabled={disabled}
            onRemove={() => removeKey(recipientKey(recipient))}
          />
        ))}
        <div className="flex min-w-[10rem] flex-1 items-center gap-2">
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
            placeholder={
              value.length > 0
                ? "Add another recipient…"
                : "Search by name, firm, favorite list, or type an email…"
            }
            aria-label={ariaLabel ?? "Recipients"}
            autoComplete="off"
            className="min-w-0 flex-1 bg-transparent text-[13px] text-[var(--text,#0f172a)] outline-none placeholder:text-[var(--text-muted,#94a3b8)]"
          />
        </div>
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
            addedKeys={existingKeys}
            onHover={setActiveIdx}
            onPick={commitOption}
          />
        </div>
      ) : null}

      {modal}
    </div>
  );
}

function RecipientChip({
  value,
  onRemove,
  disabled,
}: {
  value: RecipientValue;
  onRemove: () => void;
  disabled?: boolean;
}) {
  const email = recipientEmail(value);
  const Icon = value.kind === "adhoc" ? Send : Mail;
  return (
    <span
      title={email}
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] py-1 pl-2.5 pr-1 text-[12px]"
    >
      <Icon className="h-3 w-3 shrink-0 text-[var(--accent,#6366f1)]" />
      <span className="truncate font-medium text-[var(--text,#0f172a)]">
        {recipientLabel(value)}
      </span>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onRemove();
        }}
        disabled={disabled}
        aria-label={`Remove ${email}`}
        className="grid h-4 w-4 shrink-0 place-items-center rounded-full text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface,#ffffff)] hover:text-[var(--red,#ef4444)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        <X className="h-3 w-3" strokeWidth={2} />
      </button>
    </span>
  );
}

function DropdownSections({
  options,
  activeIdx,
  addedKeys,
  onHover,
  onPick,
}: {
  options: DropdownOption[];
  activeIdx: number;
  addedKeys: Set<string>;
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
                added={
                  opt.kind === "contact" &&
                  addedKeys.has(opt.item.contact_email.toLowerCase())
                }
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
  added,
  onHover,
  onPick,
}: {
  opt: DropdownOption;
  active: boolean;
  added: boolean;
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
        aria-disabled={added}
        onMouseDown={onMouseDown}
        onMouseEnter={onHover}
        className={`${baseClass} ${added ? "opacity-60" : ""}`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-semibold text-[var(--text,#0f172a)]">
            {c.contact_name || c.contact_email}
          </span>
          {added ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
              Added
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
              {ENTITY_KIND_LABEL[c.entity_kind]}
            </span>
          )}
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

// ── Modals ────────────────────────────────────────────────────────────

function FirmContactsDialog({
  entityKind,
  entityId,
  entityName,
  existingKeys,
  onAdd,
  onClose,
}: {
  entityKind: FirmSearchResult["entity_kind"];
  entityId: number;
  entityName: string;
  existingKeys: Set<string>;
  onAdd: (contacts: RecipientSearchResult[]) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<RecipientSearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
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

  const all = items ?? [];
  const isAdded = (c: RecipientSearchResult) =>
    existingKeys.has(c.contact_email.toLowerCase());
  // Only not-yet-added contacts are selectable; already-added ones show
  // a static "Added" marker.
  const selectable = all.filter((c) => !isAdded(c));
  const allSelected =
    selectable.length > 0 && selectable.every((c) => selected.has(c.contact_id));

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(
      allSelected ? new Set() : new Set(selectable.map((c) => c.contact_id)),
    );
  }

  function handleAdd() {
    const chosen = all.filter((c) => selected.has(c.contact_id));
    if (chosen.length > 0) onAdd(chosen);
  }

  return (
    <ModalShell
      title="Add recipients"
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
        <>
          <div className="mb-2 flex items-center justify-between">
            <label className="inline-flex cursor-pointer items-center gap-2 text-[12px] font-medium text-[var(--text-dim,#475569)]">
              <input
                type="checkbox"
                checked={allSelected}
                disabled={selectable.length === 0}
                onChange={toggleAll}
                className="h-4 w-4 accent-[var(--accent,#6366f1)]"
              />
              Select all
            </label>
            <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
              {selected.size} selected
            </span>
          </div>
          <ul className="max-h-[55vh] divide-y divide-[var(--border,rgba(30,64,175,0.1))] overflow-auto rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))]">
            {all.map((contact) => {
              const added = isAdded(contact);
              const checked = added || selected.has(contact.contact_id);
              return (
                <li key={contact.contact_id}>
                  <label
                    className={`flex w-full items-start gap-3 px-3 py-2 text-left text-[13px] transition ${
                      added
                        ? "cursor-default opacity-60"
                        : "cursor-pointer hover:bg-[var(--surface-2,#f1f6fd)]"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={added}
                      onChange={() => toggle(contact.contact_id)}
                      className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent,#6366f1)]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate font-semibold text-[var(--text,#0f172a)]">
                          {contact.contact_name || contact.contact_email}
                        </span>
                        {added ? (
                          <span className="shrink-0 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--text-dim,#475569)]">
                            Added
                          </span>
                        ) : contact.contact_title ? (
                          <span className="shrink-0 text-[11px] text-[var(--text-dim,#475569)]">
                            {contact.contact_title}
                          </span>
                        ) : null}
                      </span>
                      <span className="mt-0.5 flex items-center gap-1 text-[11px] text-[var(--text-dim,#475569)]">
                        <Mail className="h-3 w-3 text-[var(--text-muted,#94a3b8)]" />
                        <span className="truncate">{contact.contact_email}</span>
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          <div className="mt-4 flex items-center justify-end gap-3">
            <button type="button" onClick={onClose} className={OUTLINE_BTN}>
              Cancel
            </button>
            <button
              type="button"
              onClick={handleAdd}
              disabled={selected.size === 0}
              className={PRIMARY_BTN}
            >
              Add {selected.size > 0 ? selected.size : ""} recipient
              {selected.size === 1 ? "" : "s"}
            </button>
          </div>
        </>
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
