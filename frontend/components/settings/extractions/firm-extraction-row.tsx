"use client";

import {
  ChevronDown,
  ChevronRight,
  Globe,
  Linkedin,
  Loader2,
  Mail,
  Phone,
} from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { Pill } from "@/components/ui/pill";
import {
  getFirmExtractionValues,
  type ExtractedValue,
  type FirmExtractionRow,
  type ValueKind,
} from "@/lib/extraction-analytics";

const FIRM_TYPE_LABEL: Record<FirmExtractionRow["firm_type"], string> = {
  broker_dealer: "Broker-Dealer",
  advisor: "Investment Advisor",
  institutional_investor: "Institutional Investor",
};

function firmProfileHref(firm: FirmExtractionRow): Route {
  switch (firm.firm_type) {
    case "broker_dealer":
      return `/master-list/${firm.firm_id}` as Route;
    case "advisor":
      return `/advisor-list/${firm.firm_id}` as Route;
    case "institutional_investor":
      return `/institutional-investors/${firm.firm_id}` as Route;
  }
}

const KIND_ICON: Record<ValueKind, typeof Mail> = {
  email: Mail,
  phone: Phone,
  linkedin: Linkedin,
  website: Globe,
};

function ValueIcon({ kind }: { kind: ValueKind }): React.ReactElement {
  const Icon = KIND_ICON[kind];
  return (
    <Icon
      className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted,#94a3b8)]"
      strokeWidth={2}
      aria-hidden
    />
  );
}

function isHref(value: string): boolean {
  return value.startsWith("http://") || value.startsWith("https://");
}

interface FirmExtractionRowItemProps {
  firm: FirmExtractionRow;
}

export function FirmExtractionRowItem({
  firm,
}: FirmExtractionRowItemProps): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const [values, setValues] = useState<ExtractedValue[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadValues = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getFirmExtractionValues(
        firm.firm_type,
        firm.firm_id,
      );
      setValues(response.values);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load extracted values",
      );
    } finally {
      setLoading(false);
    }
  }, [firm.firm_type, firm.firm_id]);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      if (next && values === null && !loading) {
        void loadValues();
      }
      return next;
    });
  }, [values, loading, loadValues]);

  // Group the flat value list by provider for the expanded view.
  const grouped = useMemo(() => {
    if (!values) return [];
    const byProvider = new Map<string, { label: string; items: ExtractedValue[] }>();
    for (const value of values) {
      const bucket = byProvider.get(value.provider);
      if (bucket) {
        bucket.items.push(value);
      } else {
        byProvider.set(value.provider, { label: value.label, items: [value] });
      }
    }
    return Array.from(byProvider.entries())
      .map(([provider, group]) => ({ provider, ...group }))
      .sort((a, b) => b.items.length - a.items.length);
  }, [values]);

  return (
    <div className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-4 first:border-t-0">
      <div className="flex flex-wrap items-start gap-3">
        <button
          type="button"
          onClick={handleToggle}
          className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)]"
          aria-label={expanded ? "Collapse extracted values" : "Expand extracted values"}
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" strokeWidth={2} />
          ) : (
            <ChevronRight className="h-4 w-4" strokeWidth={2} />
          )}
        </button>
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <Pill variant="unknown">{FIRM_TYPE_LABEL[firm.firm_type]}</Pill>
            {firm.crd_number ? (
              <span className="text-[11px] tabular-nums text-[var(--text-muted,#94a3b8)]">
                CRD {firm.crd_number}
              </span>
            ) : null}
          </div>
          <Link
            href={firmProfileHref(firm)}
            className="mb-1.5 block text-left text-[14px] font-semibold text-[var(--text,#0f172a)] transition hover:text-[#6366f1]"
          >
            {firm.name}
          </Link>
          {firm.provider_counts.length > 0 ? (
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              {firm.provider_counts.map((pc) => (
                <Pill key={pc.provider} variant="info">
                  {pc.label}
                  <span className="tabular-nums opacity-70">× {pc.count}</span>
                </Pill>
              ))}
            </div>
          ) : null}
          <p className="text-[12px] leading-5 text-[var(--text-dim,#475569)]">
            <span className="tabular-nums">{firm.contact_total.toLocaleString()}</span>{" "}
            contact{firm.contact_total === 1 ? "" : "s"}
            {" · "}
            <span className="tabular-nums">{firm.discovered_email_total.toLocaleString()}</span>{" "}
            discovered email{firm.discovered_email_total === 1 ? "" : "s"}
            {firm.website_source ? (
              <>
                {" · "}
                website via{" "}
                <span className="font-semibold text-[var(--text-dim,#475569)]">
                  {firm.website_source}
                </span>
              </>
            ) : null}
          </p>
        </div>
      </div>

      {expanded ? (
        <div className="ml-10 mt-3">
          {loading ? (
            <div className="flex items-center gap-2 py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
              Loading extracted values…
            </div>
          ) : error ? (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
              {error}
            </p>
          ) : values === null ? null : values.length === 0 ? (
            <p className="py-2 text-[12px] text-[var(--text-muted,#94a3b8)]">
              No extracted values on file.
            </p>
          ) : (
            <div className="space-y-3">
              {grouped.map((group) => (
                <div
                  key={group.provider}
                  className="rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-3"
                >
                  <div className="mb-2 flex items-center gap-2">
                    <Pill variant="fd">{group.label}</Pill>
                    <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                      {group.items.length} value{group.items.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <ul className="space-y-1.5">
                    {group.items.map((value, index) => (
                      <li
                        key={`${value.kind}-${value.value}-${index}`}
                        className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px]"
                      >
                        <ValueIcon kind={value.kind} />
                        {isHref(value.value) ? (
                          <a
                            href={value.value}
                            target="_blank"
                            rel="noreferrer"
                            className="break-all font-medium text-[#6366f1] hover:underline"
                          >
                            {value.value}
                          </a>
                        ) : (
                          <span className="break-all font-medium text-[var(--text,#0f172a)]">
                            {value.value}
                          </span>
                        )}
                        {value.contact_name ? (
                          <span className="text-[var(--text-muted,#94a3b8)]">
                            — {value.contact_name}
                            {value.contact_title ? `, ${value.contact_title}` : ""}
                          </span>
                        ) : null}
                        {value.confidence !== null ? (
                          <span className="tabular-nums text-[var(--text-muted,#94a3b8)]">
                            ({Math.round(value.confidence)}%)
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
