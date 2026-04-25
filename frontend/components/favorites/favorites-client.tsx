"use client";

import Link from "next/link";
import { ArrowRight, Heart, Star } from "lucide-react";
import { useEffect, useState } from "react";

import { Pill, type PillVariant } from "@/components/ui/pill";
import { SectionPanel } from "@/components/ui/section-panel";
import { useToast } from "@/components/ui/use-toast";
import {
  listFavorites,
  removeFavorite,
  type FavoriteListItem,
} from "@/lib/favorites";
import { formatCurrency, formatPercent, formatRelativeTime } from "@/lib/format";

const PAGE_SIZE = 25;

// ── Backend-enum → Pill variant / label mappings ──────────────────────────
// Mirror the helpers in master-list-workspace-client.tsx so the favorites
// row reads identically to the master-list table cells.

type PriorityKey = "hot" | "warm" | "cold" | "unknown";

function resolvePriority(raw: string | null): PriorityKey {
  if (raw === "hot" || raw === "warm" || raw === "cold") return raw;
  return "unknown";
}

const PRIORITY_PILL_VARIANT: Record<PriorityKey, PillVariant> = {
  hot: "hot",
  warm: "warm",
  cold: "cold",
  unknown: "unknown",
};

const PRIORITY_PILL_LABEL: Record<PriorityKey, string> = {
  hot: "Hot",
  warm: "Warm",
  cold: "Cold",
  unknown: "Not scored",
};

const PRIORITY_DOT_CLASS: Record<PriorityKey, string> = {
  hot: "bg-[var(--red,#ef4444)] shadow-[0_0_0_4px_rgba(239,68,68,0.15)]",
  warm: "bg-[var(--amber,#f59e0b)] shadow-[0_0_0_4px_rgba(245,158,11,0.15)]",
  cold: "bg-[var(--blue,#3b82f6)] shadow-[0_0_0_4px_rgba(59,130,246,0.15)]",
  unknown:
    "bg-[var(--text-muted,#94a3b8)] shadow-[0_0_0_4px_rgba(148,163,184,0.15)]",
};

function healthVariant(status: string | null): PillVariant {
  if (status === "healthy") return "healthy";
  if (status === "ok") return "ok";
  if (status === "at_risk") return "risk";
  return "unknown";
}

function healthLabel(status: string | null): string {
  if (status === "healthy") return "Healthy";
  if (status === "ok") return "OK";
  if (status === "at_risk") return "At Risk";
  return "Unknown";
}

function formatLocation(city: string | null, state: string | null): string {
  const parts = [city, state].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(", ") : "Location not on file";
}

export function FavoritesClient() {
  const [items, setItems] = useState<FavoriteListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);
  const toast = useToast();

  useEffect(() => {
    let active = true;
    setLoading(true);

    listFavorites({ limit: PAGE_SIZE, offset: 0 })
      .then((response) => {
        if (!active) return;
        setItems(response.items);
        setTotal(response.total);
        setOffset(response.items.length);
      })
      .catch((err) => {
        if (!active) return;
        toast.error(
          err instanceof Error
            ? err.message
            : "Couldn't load favorites — please refresh.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [toast]);

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await listFavorites({ limit: PAGE_SIZE, offset });
      setItems((current) => [...current, ...response.items]);
      setTotal(response.total);
      setOffset((current) => current + response.items.length);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't load more favorites.",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function unfavorite(bdId: number) {
    if (removing !== null) return;
    const snapshot = items;
    setRemoving(bdId);
    setItems((current) => current.filter((item) => item.id !== bdId));
    setTotal((current) => Math.max(0, current - 1));
    try {
      await removeFavorite(bdId);
    } catch (err) {
      setItems(snapshot);
      setTotal(snapshot.length);
      toast.error(
        err instanceof Error
          ? err.message
          : "Couldn't unfavorite — please try again.",
      );
    } finally {
      setRemoving(null);
    }
  }

  const hasMore = items.length < total;

  return (
    <>
      {/* ── Live-match pill row (mirrors alerts / email-extractor) ──────── */}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-[12px] text-[var(--text-muted,#94a3b8)]">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-2.5 py-[3px] text-[11px] font-semibold text-[var(--text-dim,#475569)]">
          <span aria-hidden className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-[var(--green,#10b981)] opacity-60" />
            <span className="relative h-2 w-2 rounded-full bg-[var(--green,#10b981)]" />
          </span>
          {total.toLocaleString()} favorite{total === 1 ? "" : "s"} saved
        </span>
        {items.length > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-[rgba(99,102,241,0.25)] bg-[rgba(99,102,241,0.08)] px-2.5 py-[3px] text-[11px] font-semibold text-[#4338ca]">
            {items.length.toLocaleString()} loaded on this page
          </span>
        ) : null}
      </div>

      {/* ── Saved-firms SectionPanel ────────────────────────────────────── */}
      <SectionPanel eyebrow="Workspace" title="Saved firms">
        {loading ? (
          <div>
            {Array.from({ length: 6 }).map((_, index) => (
              <div
                key={`favorite-loading-${index}`}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0"
              >
                <div className="h-3 w-32 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-4 w-48 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
                <div className="mt-2 h-3 w-64 animate-pulse rounded bg-[var(--surface-2,#f1f6fd)]" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          <div>
            {items.map((item) => (
              <FavoriteRow
                key={item.id}
                item={item}
                onRemove={() => void unfavorite(item.id)}
                removing={removing === item.id}
              />
            ))}
          </div>
        )}
      </SectionPanel>

      {/* ── Load-more (matches master-list / alerts paginator buttons) ──── */}
      {hasMore && !loading ? (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={loadingMore}
            className="rounded-[8px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </>
  );
}

function FavoriteRow({
  item,
  onRemove,
  removing,
}: {
  item: FavoriteListItem;
  onRemove: () => void;
  removing: boolean;
}) {
  const priorityKey = resolvePriority(item.lead_priority);
  const priorityLabel =
    priorityKey === "unknown"
      ? PRIORITY_PILL_LABEL.unknown
      : `${PRIORITY_PILL_LABEL[priorityKey]}${
          item.lead_score !== null ? ` · ${item.lead_score.toFixed(0)}` : ""
        }`;

  return (
    <div className="flex gap-3 border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0">
      <span
        aria-hidden
        className={`mt-2 h-2 w-2 shrink-0 rounded-full ${PRIORITY_DOT_CLASS[priorityKey]}`}
      />
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex flex-wrap items-center gap-2">
          <Pill variant={PRIORITY_PILL_VARIANT[priorityKey]}>{priorityLabel}</Pill>
          <Pill variant={healthVariant(item.health_status)}>
            {healthLabel(item.health_status)}
          </Pill>
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted,#94a3b8)]">
            added {formatRelativeTime(item.favorited_at)}
          </span>
        </div>
        <Link
          href={`/master-list/${item.id}`}
          className="mb-1 block text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
        >
          {item.name}
        </Link>
        <p className="text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          <span>{formatLocation(item.city, item.state)}</span>
          {item.current_clearing_partner ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                Clearing:{" "}
                <span className="text-[var(--text,#0f172a)]">
                  {item.current_clearing_partner}
                </span>
              </span>
            </>
          ) : null}
          {item.latest_net_capital !== null ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                Net capital:{" "}
                <span className="tabular-nums text-[var(--text,#0f172a)]">
                  {formatCurrency(item.latest_net_capital)}
                </span>
              </span>
            </>
          ) : null}
          {item.yoy_growth !== null ? (
            <>
              {" "}
              <span aria-hidden className="text-[var(--text-muted,#94a3b8)]">·</span>{" "}
              <span>
                YoY:{" "}
                <span className="tabular-nums text-[var(--text,#0f172a)]">
                  {formatPercent(item.yoy_growth)}
                </span>
              </span>
            </>
          ) : null}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            href={`/master-list/${item.id}`}
            className="inline-flex items-center gap-1 rounded-md border border-[rgba(99,102,241,0.3)] px-2.5 py-1 text-[11px] font-semibold text-[#6366f1] transition hover:bg-[rgba(99,102,241,0.05)]"
          >
            Review
            <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
          </Link>
          <button
            type="button"
            onClick={onRemove}
            disabled={removing}
            aria-label={`Remove ${item.name} from favorites`}
            className="inline-flex items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-transparent px-2.5 py-1 text-[11px] font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            <Heart
              className="h-3.5 w-3.5"
              strokeWidth={2}
              fill="currentColor"
              aria-hidden
            />
            {removing ? "Removing…" : "Unfavorite"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="my-2 flex flex-col items-center gap-3 rounded-lg border border-dashed border-[var(--border,rgba(30,64,175,0.1))] px-4 py-10 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-[var(--surface-2,#f1f6fd)] text-[var(--text-muted,#94a3b8)]">
        <Star className="h-5 w-5" strokeWidth={1.75} aria-hidden />
      </div>
      <h2 className="text-[14px] font-semibold text-[var(--text,#0f172a)]">
        No favorites yet
      </h2>
      <p className="max-w-sm text-[13px] leading-5 text-[var(--text-dim,#475569)]">
        Open a firm on the master list and tap the heart to start building your
        shortlist.
      </p>
      <Link
        href="/master-list"
        className="mt-1 inline-flex h-[34px] items-center justify-center gap-1.5 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 text-[12px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:shadow-[0_8px_22px_rgba(99,102,241,0.45)]"
      >
        Browse the Master List
        <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
      </Link>
    </div>
  );
}
