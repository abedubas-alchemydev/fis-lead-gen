"use client";

import { ScanDetailView } from "@/components/email-extractor/scan-detail-view";
import { SectionPanel } from "@/components/ui/section-panel";

import { FindEmailsButton } from "./find-emails-button";

// Inline scan-results section rendered below "Filing History" on
// /master-list/{id} and below "Recent regulatory filings" on
// /advisor-list/{id}. Owns the "Find emails" trigger in its header and
// flips between an empty/hydrating SectionPanel and the live
// ScanDetailView once `currentScanId` is set.
//
// Hydration + URL sync live in the parent client (broker-dealer-detail-client
// or advisor-detail-client) via the list-scans-by-entity helper and a
// router.replace effect on `?scanId=`. This component is purely presentational
// so it stays trivially testable.
export interface EmailScansSectionProps {
  entityKind: "bd" | "advisor" | "bank";
  entityId: number;
  currentScanId: number | null;
  resolvedDomain: string | null;
  isHydrating: boolean;
  onScanCreated: (scanId: number) => void;
}

export function EmailScansSection({
  entityKind,
  entityId,
  currentScanId,
  resolvedDomain,
  isHydrating,
  onScanCreated,
}: EmailScansSectionProps): React.ReactElement {
  if (currentScanId !== null) {
    return <ScanDetailView scanId={currentScanId} />;
  }

  return (
    <SectionPanel
      eyebrow="Discovered Emails"
      title="Per-firm scan history"
      headerAction={
        <FindEmailsButton
          entityKind={entityKind}
          entityId={entityId}
          resolvedDomain={resolvedDomain}
          onScanCreated={onScanCreated}
        />
      }
    >
      {isHydrating ? (
        <div className="h-24 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
      ) : (
        <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
          {resolvedDomain
            ? "No email scans for this firm yet — click Find emails to start one."
            : "No domain on file for this firm. Find emails won't be available until a website or contact email is set."}
        </div>
      )}
    </SectionPanel>
  );
}
