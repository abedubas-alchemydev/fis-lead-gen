"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";

import { fetchInstitutionalInvestors } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type {
  InstitutionalInvestorListItem,
  InstitutionalInvestorListResponse,
} from "@/lib/types";

// Workspace client for /institutional-investors -- the 13F-filer firm
// list. Mirrors advisor-list-workspace-client at a lower fidelity: data
// path + table + simple search + pagination. Rows link to
// /institutional-investors/{id}, which mounts the firm detail page
// (with the "Generate More Details" PDL/Apollo enrich form).
export function InstitutionalInvestorsWorkspaceClient() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const page = Number(searchParams.get("page") ?? "1") || 1;
  const limit = Number(searchParams.get("limit") ?? "25") || 25;
  const search = searchParams.get("q") ?? "";

  const [data, setData] = useState<InstitutionalInvestorListResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingSearch, setPendingSearch] = useState(search);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchInstitutionalInvestors({
      q: search || undefined,
      page,
      limit,
      sort_by: "total_aum",
      sort_dir: "desc",
    })
      .then((response) => {
        if (cancelled) return;
        setData(response);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load investors");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, limit, search]);

  const totalPages = data?.meta?.total_pages ?? 1;

  const updateUrl = (updates: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value === undefined || value === "") params.delete(key);
      else params.set(key, String(value));
    }
    const query = params.toString();
    router.replace(
      (query
        ? `/institutional-investors?${query}`
        : "/institutional-investors") as Route,
    );
  };

  const submitSearch = () => {
    updateUrl({ q: pendingSearch || undefined, page: 1 });
  };

  return (
    <div className="px-7 pb-12 pt-7 animate-fade-in lg:px-9">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard
          </p>
          <h1 className="mt-1 text-[24px] font-semibold text-[var(--text-strong,#0f172a)]">
            Institutional Investors
          </h1>
          <p className="mt-1 text-sm text-[var(--text-muted,#94a3b8)]">
            13F-HR filers (institutional investment managers with
            $100M+ in qualified securities).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="search"
            value={pendingSearch}
            onChange={(e) => setPendingSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitSearch();
            }}
            placeholder="Search by name or CIK"
            className="w-64 rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-2 text-sm focus:border-[var(--accent,#3b82f6)] focus:outline-none"
          />
          <button
            type="button"
            onClick={submitSearch}
            className="rounded-md bg-[var(--accent,#3b82f6)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--accent-strong,#2563eb)]"
          >
            Search
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </div>
      )}

      <div
        className="overflow-hidden rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
      >
        <table className="w-full text-sm">
          <thead className="bg-[var(--surface-2,#f1f6fd)] text-left text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">CIK</th>
              <th className="px-4 py-3">Location</th>
              <th className="px-4 py-3 text-right">Total AUM</th>
              <th className="px-4 py-3">Latest 13F</th>
              <th className="px-4 py-3">Also RIA</th>
            </tr>
          </thead>
          <tbody>
            {loading && !data && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted,#94a3b8)]">
                  Loading...
                </td>
              </tr>
            )}
            {!loading && data && data.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--text-muted,#94a3b8)]">
                  No institutional investors found.
                </td>
              </tr>
            )}
            {data?.items.map((row) => (
              <InvestorRow key={row.id} row={row} />
            ))}
          </tbody>
        </table>
      </div>

      {data && totalPages > 1 && (
        <div className="mt-4 flex items-center justify-between text-sm text-[var(--text-muted,#94a3b8)]">
          <div>
            Page {page} of {totalPages} — {data.meta.total.toLocaleString()} total
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => updateUrl({ page: page - 1 })}
              className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-1 disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => updateUrl({ page: page + 1 })}
              className="rounded-md border border-[var(--border,rgba(30,64,175,0.1))] px-3 py-1 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function InvestorRow({ row }: { row: InstitutionalInvestorListItem }) {
  const href = `/institutional-investors/${row.id}` as Route;
  return (
    <tr className="border-t border-[var(--border,rgba(30,64,175,0.1))] hover:bg-[var(--surface-2,#f1f6fd)]">
      <td className="px-4 py-3">
        <Link href={href} className="font-medium text-[var(--accent,#3b82f6)] hover:underline">
          {row.name}
        </Link>
      </td>
      <td className="px-4 py-3 font-mono text-[12px] text-[var(--text-muted,#94a3b8)]">
        {row.cik ?? "—"}
      </td>
      <td className="px-4 py-3 text-[var(--text-muted,#94a3b8)]">
        {[row.city, row.state].filter(Boolean).join(", ") || "—"}
      </td>
      <td className="px-4 py-3 text-right font-mono">
        {row.total_aum != null ? formatCurrency(row.total_aum) : "—"}
      </td>
      <td className="px-4 py-3 text-[var(--text-muted,#94a3b8)]">
        {row.latest_13f_filing_date ? formatDate(row.latest_13f_filing_date) : "—"}
      </td>
      <td className="px-4 py-3">
        {row.advisor_id != null ? (
          <span className="rounded-full bg-[rgba(34,197,94,0.12)] px-2 py-0.5 text-[12px] font-medium text-[var(--pill-green-text,#15803d)]">
            RIA
          </span>
        ) : (
          <span className="text-[var(--text-muted,#94a3b8)]">—</span>
        )}
      </td>
    </tr>
  );
}
