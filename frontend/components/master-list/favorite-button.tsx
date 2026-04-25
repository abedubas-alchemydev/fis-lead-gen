"use client";

import { Heart } from "lucide-react";
import { useState } from "react";

import { addFavorite, removeFavorite } from "@/lib/favorites";

// Heart toggle rendered next to the firm name on /master-list/{id}. The
// optimistic flip lets the UI feel instant; a failure rolls the state back
// and surfaces an inline caption since the app doesn't yet have a shared
// toast primitive. Follow-up: swap the caption for a real toast once the
// broader UX system lands a pattern.
//
// Behaviour contract (must not regress): optimistic flip on click, rollback
// + inline error caption on failure, aria-pressed reflects current state,
// disabled while a request is in flight. Visual treatment moved off the old
// dark-navy hero onto the project's neutral surface tokens so the button
// reads on the light topbar pattern shared with /dashboard and /master-list.

export interface FavoriteButtonProps {
  bdId: number;
  initialFavorited: boolean;
  onChange?: (favorited: boolean) => void;
}

export function FavoriteButton({ bdId, initialFavorited, onChange }: FavoriteButtonProps) {
  const [favorited, setFavorited] = useState(initialFavorited);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (pending) return;

    const previous = favorited;
    const next = !previous;

    setFavorited(next);
    setPending(true);
    setError(null);
    onChange?.(next);

    try {
      if (next) {
        await addFavorite(bdId);
      } else {
        await removeFavorite(bdId);
      }
    } catch (err) {
      setFavorited(previous);
      onChange?.(previous);
      setError(err instanceof Error ? err.message : "Could not update favorite.");
    } finally {
      setPending(false);
    }
  }

  const label = favorited ? "Remove from favorites" : "Add to favorites";

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={pending}
        aria-pressed={favorited}
        aria-label={label}
        title={label}
        className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition disabled:cursor-not-allowed disabled:opacity-60 ${
          favorited
            ? "border-red-200 bg-red-500/15 text-red-500 hover:bg-red-500/20"
            : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]"
        }`}
      >
        <Heart
          className="h-5 w-5"
          strokeWidth={2}
          fill={favorited ? "currentColor" : "none"}
          aria-hidden
        />
      </button>
      {error ? <span className="text-xs text-[var(--red,#ef4444)]">{error}</span> : null}
    </div>
  );
}
