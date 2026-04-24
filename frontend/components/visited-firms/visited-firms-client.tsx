"use client";

import Link from "next/link";
import { Clock } from "lucide-react";
import { useEffect, useState } from "react";

import { HealthBadge } from "@/components/master-list/health-badge";
import { LeadPriorityBadge } from "@/components/master-list/lead-priority-badge";
import { listVisits, type VisitListItem } from "@/lib/favorites";
import { formatCurrency, formatPercent, formatRelativeTime } from "@/lib/format";

const PAGE_SIZE = 25;

function formatLocation(city: string | null, state: string | null): string {
  const parts = [city, state].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(", ") : "Location not on file";
}

function formatVisitCount(count: number): string {
  return `${count.toLocaleString()} visit${count === 1 ? "" : "s"}`;
}

export function VisitedFirmsClient() {
  const [items, setItems] = useState<VisitListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    listVisits({ limit: PAGE_SIZE, offset: 0 })
      .then((response) => {
        if (!active) return;
        setItems(response.items);
        setTotal(response.total);
        setOffset(response.items.length);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Unable to load visit history.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await listVisits({ limit: PAGE_SIZE, offset });
      setItems((current) => [...current, ...response.items]);
      setTotal(response.total);
      setOffset((current) => current + response.items.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load more visits.");
    } finally {
      setLoadingMore(false);
    }
  }

  const hasMore = items.length < total;

  return (
    <section className="space-y-4">
      <div className="rounded-[30px] border border-white/80 bg-white/92 px-6 py-4 text-sm text-slate-600 shadow-shell">
        {loading
          ? "Loading visit history…"
          : total === 0
          ? "No visited firms yet."
          : `${total.toLocaleString()} visited firm${total === 1 ? "" : "s"}`}
      </div>

      {error ? (
        <div className="rounded-[30px] border border-red-200 bg-red-50 px-6 py-4 text-sm text-danger shadow-shell">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, index) => (
            <div
              key={index}
              className="h-24 animate-pulse rounded-[28px] border border-white/80 bg-white/88 shadow-shell"
            />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id}>
              <VisitRow item={item} />
            </li>
          ))}
        </ul>
      )}

      {hasMore && !loading ? (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={loadingMore}
            className="rounded-2xl border border-slate-200 bg-white px-5 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function VisitRow({ item }: { item: VisitListItem }) {
  return (
    <article className="flex flex-wrap items-start justify-between gap-4 rounded-[28px] border border-white/80 bg-white/92 px-6 py-5 shadow-shell transition hover:bg-white">
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href={`/master-list/${item.id}`}
            className="text-lg font-semibold text-navy hover:text-blue"
          >
            {item.name}
          </Link>
          <HealthBadge status={item.health_status} />
          <LeadPriorityBadge priority={item.lead_priority} score={item.lead_score} />
        </div>
        <p className="text-sm text-slate-600">{formatLocation(item.city, item.state)}</p>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
          {item.current_clearing_partner ? (
            <span>
              Clearing: <span className="text-slate-700">{item.current_clearing_partner}</span>
            </span>
          ) : null}
          {item.latest_net_capital !== null ? (
            <span>
              Net capital:{" "}
              <span className="text-slate-700">{formatCurrency(item.latest_net_capital)}</span>
            </span>
          ) : null}
          {item.yoy_growth !== null ? (
            <span>
              YoY: <span className="text-slate-700">{formatPercent(item.yoy_growth)}</span>
            </span>
          ) : null}
        </div>
      </div>
      <div className="text-right text-xs text-slate-500">
        <p>
          last visited{" "}
          <span className="text-slate-700">{formatRelativeTime(item.last_visited_at)}</span>
        </p>
        <p className="mt-1">{formatVisitCount(item.visit_count)}</p>
      </div>
    </article>
  );
}

function EmptyState() {
  return (
    <div className="flex min-h-[340px] items-center justify-center rounded-[30px] border border-white/80 bg-white/88 p-10 shadow-shell backdrop-blur">
      <div className="flex flex-col items-center text-center">
        <div className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-500">
          <Clock className="h-6 w-6" strokeWidth={1.75} aria-hidden />
        </div>
        <h2 className="mt-5 text-lg font-semibold text-navy">Nothing here yet</h2>
        <p className="mt-2 max-w-sm text-sm text-slate-600">
          Firms you open from the master list will show up here so you can jump back in quickly.
        </p>
        <Link
          href="/master-list"
          className="mt-5 inline-flex items-center gap-2 rounded-2xl bg-navy px-4 py-2.5 text-sm font-medium text-white transition hover:bg-[#112b54]"
        >
          Browse the Master List
        </Link>
      </div>
    </div>
  );
}
