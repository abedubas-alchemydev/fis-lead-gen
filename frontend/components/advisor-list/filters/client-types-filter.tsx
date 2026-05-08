"use client";

import { useEffect, useMemo, useState } from "react";

import { apiRequest } from "@/lib/api";
import {
  MultiSelectFilter,
  type MultiSelectFilterOption,
} from "@/components/ui/multi-select-filter";
import type { ClientTypeCount } from "@/lib/types";

interface ClientTypesFilterProps {
  value: string[];
  onChange: (value: string[]) => void;
}

// Form ADV Item 5.D client-category catalog (e.g. "Pension and profit
// sharing plans", "Investment companies"). Same shape as the advisory-
// activities filter; the BE endpoint returns distinct rows with counts.
export function ClientTypesFilter({ value, onChange }: ClientTypesFilterProps) {
  const [rows, setRows] = useState<ClientTypeCount[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ClientTypeCount[]>(
      "/api/v1/investment-advisors/client-types",
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
      ? "All client types"
      : value.length === 1
      ? value[0]
      : `${value.length} selected`;

  return (
    <div>
      <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
        Client Types
      </label>
      <MultiSelectFilter
        value={value}
        onChange={onChange}
        options={options}
        triggerLabel={triggerLabel}
        placeholder="Search client types…"
        ariaLabel="Client types"
        loading={loading}
        noOptionsLabel="No client types reported"
      />
    </div>
  );
}
