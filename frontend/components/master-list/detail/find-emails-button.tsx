"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Route } from "next";
import { Search } from "lucide-react";

import { apiRequest } from "@/lib/api";

// "Find emails" button rendered in the firm-detail PDF action strip. Resolves
// the firm's domain (firm website preferred, falling back to a contact-email
// domain), kicks off a scan via
//   POST /api/v1/email-extractor/scans
// and routes to the resulting scan detail page on success. Disabled when no
// domain can be resolved or while a scan creation is in flight.
//
// Threads the firm-detail page's `?return=` envelope (originating on
// the master list) through to the scan-detail URL so the email-
// extractor breadcrumb can land the user back on the exact filtered/
// sorted master-list state they came from.
export function FindEmailsButton({
  brokerDealerId,
  resolvedDomain,
}: {
  brokerDealerId: string;
  resolvedDomain: string | null;
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disabled = !resolvedDomain || isStarting;

  async function handleClick() {
    if (!resolvedDomain) return;
    setIsStarting(true);
    setError(null);
    try {
      const created = await apiRequest<{ id: number }>("/api/v1/email-extractor/scans", {
        method: "POST",
        body: JSON.stringify({
          domain: resolvedDomain,
          bd_id: Number(brokerDealerId),
        }),
      });
      const returnRaw = searchParams.get("return");
      const destination = returnRaw
        ? `/email-extractor/${created.id}?return=${returnRaw}`
        : `/email-extractor/${created.id}`;
      router.push(destination as Route);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start scan");
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
        <span className="text-xs text-[var(--pill-red-text,#b91c1c)]">{error}</span>
      ) : null}
    </div>
  );
}
