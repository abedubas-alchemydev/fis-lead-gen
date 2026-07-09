"use client";

import { useState, type ReactNode } from "react";

// Generic paginated table for the "People" panels on both the advisor detail
// page (/advisor-list/{id}) and the broker-dealer detail page
// (/master-list/{id}). Each group (direct owners, executive officers, indirect
// owners, additional contacts) can have dozens of rows, so every group renders
// as its own paginated table. Page state lives in the table so the surrounding
// panel doesn't have to track per-group page indices.
export const PEOPLE_TABLE_PAGE_SIZE = 10;

export type PeopleColumn<T> = {
  header: string;
  cell: (item: T) => ReactNode;
  className?: string;
};

export function PeopleTable<T>({
  title,
  items,
  columns,
  pageSize = PEOPLE_TABLE_PAGE_SIZE,
  showTitle = true,
}: {
  title: string;
  items: readonly T[];
  columns: readonly PeopleColumn<T>[];
  pageSize?: number;
  // When false, the in-table "{title} ({total})" label is suppressed and only
  // the pager renders (right-aligned) — used when a surrounding panel already
  // supplies the heading (e.g. the standalone Discovered emails share section).
  // Defaults true so every existing call site renders unchanged.
  showTitle?: boolean;
}) {
  const [page, setPage] = useState(0);
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // ``items`` may shrink on a parent re-render — clamp the active page so we
  // never slice past the end after the source list got shorter.
  const safePage = Math.min(page, totalPages - 1);
  const start = safePage * pageSize;
  const visible = items.slice(start, start + pageSize);
  const showPager = total > pageSize;

  return (
    <div className="mb-5 last:mb-0">
      {showTitle || showPager ? (
        <div
          className={`mb-2 flex flex-wrap items-center gap-3 ${
            showTitle ? "justify-between" : "justify-end"
          }`}
        >
          {showTitle ? (
            <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">
              {total > 0 ? `${title} (${total})` : title}
            </p>
          ) : null}
          {showPager ? (
          <div className="flex items-center gap-2 text-xs text-[var(--text-muted,#94a3b8)]">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              className="rounded-md px-2 py-1 hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
              aria-label={`Previous page of ${title}`}
            >
              Prev
            </button>
            <span aria-live="polite">
              {start + 1}–{Math.min(start + pageSize, total)} of {total}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={safePage >= totalPages - 1}
              className="rounded-md px-2 py-1 hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
              aria-label={`Next page of ${title}`}
            >
              Next
            </button>
          </div>
          ) : null}
        </div>
      ) : null}
      <div className="overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))]">
        <table className="w-full text-sm">
          <thead className="bg-[var(--surface-2,#f1f6fd)] text-left text-xs uppercase tracking-wide text-[var(--text-muted,#94a3b8)]">
            <tr>
              {columns.map((c) => (
                <th
                  key={c.header}
                  className={`px-4 py-2 font-semibold ${c.className ?? ""}`}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((item, i) => (
              <tr
                key={start + i}
                className="border-t border-[var(--border,rgba(30,64,175,0.1))] text-[var(--text-dim,#475569)]"
              >
                {columns.map((c) => (
                  <td
                    key={c.header}
                    className={`px-4 py-2 align-top ${c.className ?? ""}`}
                  >
                    {c.cell(item)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
