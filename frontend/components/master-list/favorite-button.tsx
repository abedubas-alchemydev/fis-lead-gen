"use client";

import { Heart } from "lucide-react";
import { useState } from "react";

import { useToast } from "@/components/ui/use-toast";
import { addFavorite, removeFavorite } from "@/lib/favorites";

// Heart toggle rendered next to the firm name on /master-list/{id}. The
// optimistic flip lets the UI feel instant; a failure rolls the state back
// and surfaces a top-right toast.
//
// Behaviour contract (must not regress): optimistic flip on click, rollback
// + toast on failure, aria-pressed reflects current state, disabled while a
// request is in flight. Visual treatment moved off the old dark-navy hero
// onto the project's neutral surface tokens so the button reads on the light
// topbar pattern shared with /dashboard and /master-list.

export interface FavoriteButtonProps {
  bdId: number;
  initialFavorited: boolean;
  onChange?: (favorited: boolean) => void;
}

export function FavoriteButton({ bdId, initialFavorited, onChange }: FavoriteButtonProps) {
  const [favorited, setFavorited] = useState(initialFavorited);
  const [pending, setPending] = useState(false);
  const toast = useToast();

  async function toggle() {
    if (pending) return;

    const previous = favorited;
    const next = !previous;

    setFavorited(next);
    setPending(true);
    onChange?.(next);

    try {
      if (next) {
        await addFavorite(bdId);
      } else {
        await removeFavorite(bdId);
      }
    } catch {
      setFavorited(previous);
      onChange?.(previous);
      toast.error("Couldn't update favorite — please try again.");
    } finally {
      setPending(false);
    }
  }

  const label = favorited ? "Remove from favorites" : "Add to favorites";

  return (
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
  );
}
