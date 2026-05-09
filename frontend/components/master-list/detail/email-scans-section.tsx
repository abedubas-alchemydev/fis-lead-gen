"use client";

import { ScanDetailView } from "@/components/email-extractor/scan-detail-view";
import { SectionPanel } from "@/components/ui/section-panel";

// Inline scan-results section rendered below "Filing History" on
// /master-list/{id}. Replaces the standalone-page redirect that the
// "Find emails" button used to perform — clicking the button now mutates
// `currentScanId` on the parent detail-client and this section flips
// from empty/hydrating to the live ScanDetailView in place.
//
// Hydration + URL sync live in broker-dealer-detail-client.tsx (via the
// list-scans-by-bd_id endpoint and a router.replace effect on
// `?scanId=`). This component is purely presentational so it stays
// trivially testable.
export interface EmailScansSectionProps {
  currentScanId: number | null;
  resolvedDomain: string | null;
  isHydrating: boolean;
}

export function EmailScansSection({
  currentScanId,
  resolvedDomain,
  isHydrating,
}: EmailScansSectionProps): React.ReactElement {
  if (currentScanId !== null) {
    return <ScanDetailView scanId={currentScanId} />;
  }

  return (
    <SectionPanel
      eyebrow="Discovered Emails"
      title="Per-firm scan history"
    >
      {isHydrating ? (
        <div className="h-24 animate-pulse rounded-2xl bg-[var(--surface-2,#f1f6fd)]" />
      ) : (
        <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] px-4 py-8 text-center text-sm text-[var(--text-muted,#94a3b8)]">
          {resolvedDomain
            ? "No email scans for this firm yet — click Find emails above to start one."
            : "No domain on file for this firm. Find emails won't be available until a website or contact email is set."}
        </div>
      )}
    </SectionPanel>
  );
}
