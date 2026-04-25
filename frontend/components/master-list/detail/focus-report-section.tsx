"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";

import { apiRequest } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";
import type { FocusCeoExtractionResponse } from "@/lib/types";

// FOCUS report extraction sub-panel rendered inside the Assessment SectionPanel.
// Owns its own loading/result/error state. Triggers
//   POST /api/v1/broker-dealers/{id}/extract-focus-ceo
// then asks the parent to refresh the profile via onProfileRefresh so the
// new ExecutiveContact + net-capital make it into the rest of the page.
export function FocusReportSection({
  brokerDealerId,
  onProfileRefresh,
}: {
  brokerDealerId: string;
  onProfileRefresh: () => Promise<void>;
}) {
  const [isExtracting, setIsExtracting] = useState(false);
  const [result, setResult] = useState<FocusCeoExtractionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function extract() {
    setIsExtracting(true);
    setError(null);
    try {
      const resp = await apiRequest<FocusCeoExtractionResponse>(
        `/api/v1/broker-dealers/${brokerDealerId}/extract-focus-ceo`,
        { method: "POST" },
      );
      setResult(resp);
      await onProfileRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "FOCUS extraction failed.");
    } finally {
      setIsExtracting(false);
    }
  }

  const statusPillClass =
    result?.extraction_status === "success"
      ? "bg-[rgba(16,185,129,0.12)] text-[var(--pill-green-text,#047857)]"
      : result?.extraction_status === "error" || result?.extraction_status === "no_pdf"
      ? "bg-[rgba(239,68,68,0.12)] text-[var(--pill-red-text,#b91c1c)]"
      : "bg-[rgba(245,158,11,0.12)] text-[var(--pill-amber-text,#b45309)]";

  const statusLabel =
    result?.extraction_status === "success"
      ? "Success"
      : result?.extraction_status === "no_pdf"
      ? "No PDF"
      : result?.extraction_status === "error"
      ? "Error"
      : "Low confidence";

  return (
    <div className="mt-6 border-t border-[var(--border,rgba(30,64,175,0.1))] pt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-[var(--text,#0f172a)]">FOCUS Report Data</p>
          <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
            Extracts contact person, phone, email, and net capital from the latest X-17A-5 PDF filing on SEC EDGAR.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void extract()}
          disabled={isExtracting}
          className="inline-flex shrink-0 items-center gap-2 rounded-[10px] bg-gradient-to-br from-[#6366f1] to-[#8b5cf6] px-4 py-2 text-[13px] font-semibold text-white shadow-[0_6px_16px_rgba(99,102,241,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isExtracting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.5} />
              Extracting…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" strokeWidth={2} />
              {result ? "Re-extract" : "Extract FOCUS Data"}
            </>
          )}
        </button>
      </div>

      {error ? (
        <div className="mt-3 rounded-2xl border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="mt-3 space-y-3">
          {result.ceo_name || result.net_capital !== null ? (
            <div className="grid gap-2 md:grid-cols-2">
              {result.ceo_name ? (
                <div className="rounded-2xl bg-[rgba(16,185,129,0.08)] px-4 py-3 text-sm">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                    Contact Person
                  </p>
                  <p className="mt-1 text-[var(--text,#0f172a)]">{result.ceo_name}</p>
                  {result.ceo_title ? (
                    <p className="text-xs text-[var(--text-muted,#94a3b8)]">{result.ceo_title}</p>
                  ) : null}
                </div>
              ) : null}
              {result.ceo_phone ? (
                <div className="rounded-2xl bg-[rgba(16,185,129,0.08)] px-4 py-3 text-sm">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                    Phone
                  </p>
                  <p className="mt-1 text-[var(--text,#0f172a)]">{result.ceo_phone}</p>
                </div>
              ) : null}
              {result.ceo_email ? (
                <div className="rounded-2xl bg-[rgba(16,185,129,0.08)] px-4 py-3 text-sm">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                    Email
                  </p>
                  <a
                    href={`mailto:${result.ceo_email}`}
                    className="mt-1 block text-[var(--accent,#6366f1)] hover:underline"
                  >
                    {result.ceo_email}
                  </a>
                </div>
              ) : null}
              {result.net_capital !== null ? (
                <div className="rounded-2xl bg-[rgba(16,185,129,0.08)] px-4 py-3 text-sm">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">
                    Net Capital (from PDF)
                  </p>
                  <p className="mt-1 text-[18px] font-semibold tabular-nums text-[var(--text,#0f172a)]">
                    {formatCurrency(result.net_capital)}
                  </p>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="rounded-2xl border border-[rgba(245,158,11,0.25)] bg-[rgba(245,158,11,0.08)] px-4 py-3 text-sm text-[var(--pill-amber-text,#b45309)]">
              No contact or net capital data could be extracted from this filing.
              {result.extraction_status === "no_pdf"
                ? " This firm has no X-17A-5 PDF on EDGAR."
                : " The PDF may be scanned or use a non-standard format."}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-2 text-xs text-[var(--text-muted,#94a3b8)]">
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${statusPillClass}`}>
              {statusLabel}
            </span>
            <span>Confidence: {(result.confidence_score * 100).toFixed(0)}%</span>
            {result.report_date ? <span>Report: {formatDate(result.report_date)}</span> : null}
            {result.source_pdf_url ? (
              <a
                href={result.source_pdf_url}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--accent,#6366f1)] hover:underline"
              >
                View source PDF
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
