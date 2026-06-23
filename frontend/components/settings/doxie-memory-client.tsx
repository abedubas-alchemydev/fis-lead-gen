"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Brain, Clock, Loader2, Trash2 } from "lucide-react";

import { useToast } from "@/components/ui/use-toast";
import { ApiError, deleteDoxieMemory, getDoxieMemories } from "@/lib/api";
import type { DoxieMemoryItem } from "@/lib/types";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";

const EYEBROW =
  "text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";

const CARD_TITLE =
  "mt-1 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]";

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function DoxieMemoryClient() {
  const toast = useToast();
  const [items, setItems] = useState<DoxieMemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getDoxieMemories();
      setItems(result.items);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail || err.message
          : err instanceof Error
            ? err.message
            : "Failed to load memories."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = useCallback(
    async (memory: DoxieMemoryItem) => {
      if (!window.confirm("Delete this memory? Doxie will stop using it.")) {
        return;
      }
      const previous = items;
      // Optimistic removal; roll back on error.
      setItems((prev) => prev.filter((m) => m.id !== memory.id));
      setDeletingId(memory.id);
      try {
        await deleteDoxieMemory(memory.id);
        toast.success("Memory deleted.");
      } catch (err) {
        setItems(previous);
        const message =
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Couldn't delete memory.";
        toast.error(message);
      } finally {
        setDeletingId(null);
      }
    },
    [items, toast]
  );

  const total = items.length;

  return (
    <section className="max-w-2xl space-y-6">
      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>{error}</span>
        </div>
      ) : null}

      <div className={CARD}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className={EYEBROW}>Saved memories</p>
            <h2 className={CARD_TITLE}>
              {total === 0
                ? "Nothing saved yet"
                : `${total} ${total === 1 ? "memory" : "memories"}`}
            </h2>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.04em] text-[var(--text,#0f172a)]">
            <Brain className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            Doxie
          </span>
        </div>

        {loading ? (
          <div className="mt-6 flex items-center justify-center gap-2 py-12 text-sm text-[var(--text-dim,#475569)]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading memories&hellip;
          </div>
        ) : items.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] px-4 py-12 text-center">
            <p className="text-sm font-semibold text-[var(--text,#0f172a)]">
              Doxie hasn&apos;t saved anything about you yet.
            </p>
            <p className="mt-1 text-xs text-[var(--text-dim,#475569)]">
              Tell Doxie a durable fact or preference in chat &mdash; like the firms you focus on or how you
              like outreach drafted &mdash; and it will remember it here.
            </p>
          </div>
        ) : (
          <ul className="mt-4 space-y-2">
            {items.map((memory) => (
              <li
                key={memory.id}
                className="flex items-start justify-between gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="text-sm text-[var(--text,#0f172a)]">{memory.content}</p>
                  <p className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
                    <Clock className="h-3 w-3" aria-hidden />
                    Saved {formatDateTime(memory.created_at)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(memory)}
                  disabled={deletingId === memory.id}
                  aria-label="Delete memory"
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-2.5 py-1.5 text-xs font-semibold text-[var(--pill-red-text,#b91c1c)] transition hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {deletingId === memory.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  )}
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
