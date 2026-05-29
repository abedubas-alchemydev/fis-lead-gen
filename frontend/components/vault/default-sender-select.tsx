"use client";

import { useEffect, useState } from "react";

import { getLinkedProviders } from "@/lib/api";
import type { EmailProviderId, LinkedProviderItem } from "@/lib/types";

// Per-service default-sender picker. Lazy-loads the user's linked email
// accounts and renders them as a labelled <select>. Shared by the Vault
// create form (vault-client) and the detail editor (vault-folder-detail)
// so both surfaces offer an identical control. Failure to load the
// accounts is non-fatal — the picker just shows "None" until the user
// reopens the page.

const PROVIDER_LABEL: Record<EmailProviderId, string> = {
  google: "Gmail",
  microsoft: "Outlook",
  yahoo: "Yahoo Mail"
};

interface DefaultSenderSelectProps {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
}

export function DefaultSenderSelect({
  value,
  onChange,
  disabled = false
}: DefaultSenderSelectProps) {
  const [linkedProviders, setLinkedProviders] = useState<LinkedProviderItem[]>(
    []
  );

  // Lazy load linked accounts so the dropdown is populated. Failure is
  // non-fatal -- the picker just shows "None" until the user retries
  // by reopening the page.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getLinkedProviders();
        if (cancelled) return;
        setLinkedProviders(result.items);
      } catch {
        // No-op: the rest of the editor still works without a picker.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
      Default sender for this service
      <select
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
        disabled={disabled}
        className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <option value="">None (use first available)</option>
        {linkedProviders.map((p) => (
          <option key={p.account_id} value={p.account_id}>
            {p.email_address ?? `${PROVIDER_LABEL[p.provider]} account`}{" "}
            ({PROVIDER_LABEL[p.provider]})
          </option>
        ))}
      </select>
      <p className="mt-1 text-[11px] leading-4 text-[var(--text-muted,#94a3b8)]">
        Outreach for this service preselects this address. Users can
        override per-send.
      </p>
    </label>
  );
}
