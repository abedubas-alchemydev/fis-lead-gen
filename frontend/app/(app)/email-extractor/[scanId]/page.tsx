"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import type { Route } from "next";
import { useMemo, useState } from "react";

import { ScanDetailView } from "@/components/email-extractor/scan-detail-view";
import { type ScanResponse } from "@/lib/email-extractor";
import { formatRelativeTime } from "@/lib/format";
import { buildMasterListUrl, parseReturnParam } from "@/lib/master-list-state";

const SECONDARY_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-45";

export default function ScanDetailPage(): React.ReactElement {
  const params = useParams<{ scanId: string }>();
  const scanId = Number(params?.scanId);

  // When a `return` envelope is present (the user reached this scan via
  // "Find emails" on a firm-detail page that was itself entered from the
  // master list), wire the right-rail back-link to land them on the same
  // paginated/filtered/sorted master-list view they left. Direct visits /
  // bookmarks fall back to the email-extractor hub.
  const searchParams = useSearchParams();
  const returnState = useMemo(
    () => parseReturnParam(searchParams.get("return")),
    [searchParams]
  );
  const backLink = useMemo<{ href: Route; label: string }>(() => {
    if (returnState) {
      return {
        href: buildMasterListUrl(returnState) as Route,
        label: "Back to Master List",
      };
    }
    return {
      href: "/email-extractor" as Route,
      label: "Back to Email Extractor",
    };
  }, [returnState]);

  // ScanDetailView owns the polling fetch; it surfaces the loaded scan up
  // here so the page can render the live h1 + meta line without
  // double-fetching.
  const [scan, setScan] = useState<ScanResponse | null>(null);

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-6">
        <Link href={backLink.href} className={`${SECONDARY_BTN} mb-4`}>
          <ArrowLeft className="h-4 w-4" strokeWidth={2} />
          {backLink.label}
        </Link>
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <Link
              href={"/email-extractor" as Route}
              className="transition hover:text-[var(--text-dim,#475569)]"
            >
              Email Extractor
            </Link>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Scan
          </p>
          <h1 className="mt-1 break-all text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            {scan?.domain ?? `Scan #${scanId}`}
          </h1>
          {scan ? (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
              <span>
                scan{" "}
                <span className="font-mono text-[var(--text-dim,#475569)]">
                  #{scan.id}
                </span>
              </span>
              <span aria-hidden>·</span>
              <span>created {formatRelativeTime(scan.created_at)}</span>
              {scan.total_items > 0 ? (
                <>
                  <span aria-hidden>·</span>
                  <span className="tabular-nums">
                    {scan.processed_items} / {scan.total_items} processed
                  </span>
                </>
              ) : null}
              {scan.success_count > 0 || scan.failure_count > 0 ? (
                <>
                  <span aria-hidden>·</span>
                  <span className="tabular-nums text-[var(--pill-green-text,#047857)]">
                    {scan.success_count} success
                    {scan.success_count === 1 ? "" : "es"}
                  </span>
                  {scan.failure_count > 0 ? (
                    <span className="tabular-nums text-[var(--pill-red-text,#b91c1c)]">
                      · {scan.failure_count} failure
                      {scan.failure_count === 1 ? "" : "s"}
                    </span>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <ScanDetailView scanId={scanId} onScanLoaded={setScan} />
    </div>
  );
}
