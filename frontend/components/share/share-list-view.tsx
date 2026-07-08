"use client";

import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { ChevronRight } from "lucide-react";

import { SectionPanel } from "@/components/ui/section-panel";
import { formatCurrency } from "@/lib/format";
import type { PublicShareItemRow, PublicShareKind } from "@/types/public-share";

// Read-only list view for an unlocked share. Rows are grouped by kind in a
// fixed order (Broker-Dealers → Banks → Investment Advisors); empty groups
// are omitted entirely. Clicking a row pushes ?item=<item_id> so the
// browser Back button walks profile → list naturally.

const KIND_ORDER: PublicShareKind[] = ["broker_dealer", "bank", "advisor"];

const KIND_META: Record<
  PublicShareKind,
  { plural: string; panelTitle: string; pillLabel: string; pillClass: string }
> = {
  broker_dealer: {
    plural: "Broker-Dealers",
    panelTitle: "FINRA-registered broker-dealers",
    pillLabel: "Broker-Dealer",
    pillClass:
      "bg-[rgba(99,102,241,0.12)] text-[#4338ca] border-[rgba(99,102,241,0.25)]",
  },
  bank: {
    plural: "Banks",
    panelTitle: "Chartered banking institutions",
    pillLabel: "Bank",
    pillClass:
      "bg-[rgba(20,184,166,0.12)] text-[#0f766e] border-[rgba(20,184,166,0.25)]",
  },
  advisor: {
    plural: "Investment Advisors",
    panelTitle: "SEC-registered investment advisors",
    pillLabel: "RIA",
    pillClass:
      "bg-[rgba(16,185,129,0.12)] text-[#047857] border-[rgba(16,185,129,0.25)]",
  },
};

// formatCurrency renders null as "N/A"; this surface uses "—" for missing
// values, so wrap rather than fork the shared helper.
function money(value: number | null): string {
  return value === null ? "—" : formatCurrency(value);
}

function headline(item: PublicShareItemRow): string {
  switch (item.kind) {
    case "broker_dealer":
      return `Net capital ${money(item.latest_net_capital)} · ${item.health_status ?? "—"}`;
    case "bank":
      return `Assets ${money(item.asset)} · ${item.charter_status ?? "—"}`;
    case "advisor": {
      const clients =
        item.total_clients === null
          ? "—"
          : item.total_clients.toLocaleString("en-US");
      return `AUM ${money(item.regulatory_aum)} · ${clients} clients`;
    }
  }
}

function subline(item: PublicShareItemRow): string {
  const location = [item.city, item.state].filter(Boolean).join(", ");
  return location ? `${location} · ${headline(item)}` : headline(item);
}

export interface ShareListViewProps {
  token: string;
  shareName: string;
  items: PublicShareItemRow[];
}

export function ShareListView({ token, shareName, items }: ShareListViewProps) {
  const router = useRouter();

  const groups = useMemo(
    () =>
      KIND_ORDER.map((kind) => ({
        kind,
        rows: items
          .filter((item) => item.kind === kind)
          .sort((a, b) => a.position - b.position),
      })).filter((group) => group.rows.length > 0),
    [items]
  );

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
      <header>
        <h1 className="text-xl font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
          {shareName}
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted,#94a3b8)]">
          {items.length} firm{items.length === 1 ? "" : "s"} · shared via DOX
        </p>
      </header>

      <div className="mt-6 space-y-5">
        {groups.map(({ kind, rows }) => {
          const meta = KIND_META[kind];
          return (
            <SectionPanel
              key={kind}
              eyebrow={`${meta.plural} (${rows.length})`}
              title={meta.panelTitle}
            >
              <ul className="-mx-2 divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
                {rows.map((row) => (
                  <li key={row.item_id}>
                    <button
                      type="button"
                      onClick={() =>
                        router.push(
                          `/share/${token}?item=${row.item_id}` as Route
                        )
                      }
                      className="group flex w-full items-center gap-3 px-2 py-3 text-left transition hover:bg-[var(--surface-2,#f1f6fd)] focus:outline-none focus-visible:rounded-lg focus-visible:ring-2 focus-visible:ring-[var(--accent,#6366f1)]/30"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-semibold text-[var(--text,#0f172a)]">
                            {row.name}
                          </span>
                          <span
                            className={`inline-flex items-center whitespace-nowrap rounded-full border px-2.5 py-[3px] text-[11px] font-semibold tracking-[0.02em] ${meta.pillClass}`}
                          >
                            {meta.pillLabel}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-xs text-[var(--text-muted,#94a3b8)]">
                          {subline(row)}
                        </p>
                      </div>
                      <ChevronRight
                        className="h-4 w-4 shrink-0 text-[var(--text-muted,#94a3b8)] transition group-hover:translate-x-0.5 group-hover:text-[var(--text-dim,#475569)]"
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
            </SectionPanel>
          );
        })}
      </div>
    </div>
  );
}
