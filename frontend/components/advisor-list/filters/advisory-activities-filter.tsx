"use client";

import { useEffect, useMemo, useState } from "react";

import { apiRequest } from "@/lib/api";
import {
  MultiSelectFilter,
  type MultiSelectFilterOption,
} from "@/components/ui/multi-select-filter";
import type { AdvisoryActivityCount } from "@/lib/types";

interface AdvisoryActivitiesFilterProps {
  value: string[];
  onChange: (value: string[]) => void;
}

// Form ADV Item 5.G activity catalog (e.g. "Investment Adviser to Mutual
// Funds", "Pension Consulting Services"). Backend returns distinct rows
// with per-type counts; we render the count badge inside the dropdown
// so the user sees how many advisors carry each attribute. One-shot
// fetch on mount — the catalog is small (≤40 entries) and stable.
export function AdvisoryActivitiesFilter({
  value,
  onChange,
}: AdvisoryActivitiesFilterProps) {
  const [rows, setRows] = useState<AdvisoryActivityCount[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    apiRequest<AdvisoryActivityCount[]>(
      "/api/v1/investment-advisors/advisory-activities",
      { signal: controller.signal },
    )
      .then((response) => {
        setRows(response);
        setLoading(false);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const options = useMemo<MultiSelectFilterOption[]>(
    () => rows.map((row) => ({ value: row.type, label: row.type, count: row.count })),
    [rows],
  );

  const triggerLabel =
    value.length === 0
      ? "All advisory activities"
      : value.length === 1
      ? value[0]
      : `${value.length} selected`;

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Advisory Activities
      </label>
      <MultiSelectFilter
        value={value}
        onChange={onChange}
        options={options}
        triggerLabel={triggerLabel}
        placeholder="Search activities…"
        ariaLabel="Advisory Activities"
        loading={loading}
        noOptionsLabel="No activities reported"
      />
    </div>
  );
}
