"use client";

import { Plus } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";

const MAX_NAME_LENGTH = 80;

// Header strip for the favorite-lists sidebar: "Lists" label, "+ New list"
// trigger, and the inline create form that drops in below when the trigger
// is clicked. Keeps state local so the rest of the sidebar can stay
// presentational; phase-2 (#17) only.
export function NewListButton({
  onCreate,
}: {
  onCreate: (name: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function close() {
    setOpen(false);
    setValue("");
    setError(null);
    setSubmitting(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;

    const trimmed = value.trim();
    if (trimmed.length === 0) {
      setError("Name can't be empty.");
      return;
    }
    if (trimmed.length > MAX_NAME_LENGTH) {
      setError(`Name must be ${MAX_NAME_LENGTH} characters or fewer.`);
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await onCreate(trimmed);
      close();
    } catch (err) {
      setSubmitting(false);
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : null;
      setError(message || "Couldn't create list.");
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
          Lists
        </h3>
        <button
          type="button"
          onClick={() => setOpen((next) => !next)}
          aria-expanded={open}
          className="inline-flex h-[26px] items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={2.25} aria-hidden />
          New list
        </button>
      </div>

      {open ? (
        <form onSubmit={handleSubmit} className="space-y-1.5" noValidate>
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                close();
              }
            }}
            placeholder="New list name"
            maxLength={MAX_NAME_LENGTH}
            aria-label="New list name"
            aria-invalid={error ? true : undefined}
            disabled={submitting}
            className="block w-full rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1.5 text-[13px] text-[var(--text,#0f172a)] placeholder:text-[var(--text-muted,#94a3b8)] focus:border-[var(--accent,#6366f1)] focus:outline-none focus:ring-2 focus:ring-[rgba(99,102,241,0.2)] disabled:opacity-60"
          />
          <div className="flex items-center justify-end gap-1.5">
            <button
              type="button"
              onClick={close}
              disabled={submitting}
              className="inline-flex h-[26px] items-center rounded-md border border-transparent px-2 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || value.trim().length === 0}
              className="inline-flex h-[26px] items-center rounded-md border border-[rgba(99,102,241,0.4)] bg-[rgba(99,102,241,0.08)] px-2.5 text-[11px] font-semibold text-[#4338ca] transition hover:bg-[rgba(99,102,241,0.14)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Save"}
            </button>
          </div>
          {error ? (
            <p
              role="alert"
              className="text-[11px] leading-4 text-[var(--red,#dc2626)]"
            >
              {error}
            </p>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
