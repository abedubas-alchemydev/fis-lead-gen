"use client";

import "@/components/security/security.css";

import { useInputGuards } from "@/components/security/use-input-guards";
import { WatermarkOverlay } from "@/components/security/watermark-overlay";

// Public-share twin of SecurityShield (components/security/security-shield.tsx).
// That component is session-shaped (email/id/name from the auth session);
// the share viewer has no user, so the watermark is keyed on the share
// token + share name instead. Composes the same two shared concerns —
// security.css + useInputGuards() side effects, and the watermark overlay —
// and is mounted from FIRST PAINT inside ShareClient so even the password
// gate renders watermarked and guard-protected.
//
// Note: useInputGuards' blocked-action toasts render via the <Toaster />
// mounted in app/share/layout.tsx; its audit reporting (report-event.ts)
// is best-effort sendBeacon and never throws on this anonymous surface.

export interface ShareShieldProps {
  token: string;
  shareName?: string | null;
}

export function ShareShield({ token, shareName }: ShareShieldProps) {
  useInputGuards();

  return (
    <WatermarkOverlay
      email="shared-viewer"
      userId={token}
      name={shareName ? `Confidential · ${shareName}` : "Confidential"}
    />
  );
}
