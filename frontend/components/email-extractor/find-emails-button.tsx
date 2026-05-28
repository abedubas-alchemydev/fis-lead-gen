"use client";

import { useState } from "react";
import { Search } from "lucide-react";

import { apiRequest } from "@/lib/api";

// "Find emails" button rendered in the entity-detail "Discovered Emails"
// section header. Resolves the entity's domain (firm website preferred,
// falling back to a contact-email domain), kicks off a scan via
//   POST /api/v1/email-extractor/scans
// with either `bd_id` or `advisor_id` depending on `entityKind`, and
// notifies the parent via `onScanCreated` so the inline scan-results
// section on the same page can render the new scan in place. Disabled
// when no domain can be resolved or while a scan creation is in flight.
export function FindEmailsButton({
  entityKind,
  entityId,
  resolvedDomain,
  onScanCreated,
}: {
  entityKind: "bd" | "advisor";
  entityId: number;
  resolvedDomain: string | null;
  onScanCreated: (scanId: number) => void;
}) {
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disabled = !resolvedDomain || isStarting;

  async function handleClick() {
    if (!resolvedDomain) return;
    setIsStarting(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { domain: resolvedDomain };
      if (entityKind === "bd") body.bd_id = entityId;
      else body.advisor_id = entityId;
      const created = await apiRequest<{ id: number }>(
        "/api/v1/email-extractor/scans",
        {
          method: "POST",
          body: JSON.stringify(body),
        }
      );
      onScanCreated(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start scan");
    } finally {
      setIsStarting(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={disabled}
        title={
          resolvedDomain
            ? `Scan ${resolvedDomain} for contact emails`
            : "No domain on file for this firm"
        }
        className="inline-flex items-center gap-1.5 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-3.5 py-2 text-[12px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Search className="h-3.5 w-3.5" strokeWidth={2.5} />
        {isStarting ? "Starting…" : "Find emails"}
      </button>
      {error ? (
        <span className="text-xs text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </span>
      ) : null}
    </div>
  );
}
