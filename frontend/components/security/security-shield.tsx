"use client";

import "./security.css";

import { useDevtoolsDetector } from "./use-devtools-detector";
import { useInputGuards } from "./use-input-guards";
import { WatermarkOverlay } from "./watermark-overlay";

export interface SecurityShieldProps {
  userEmail: string;
  userId: string;
  userName?: string;
}

// Mount once inside the gated app layout. Composes the three concerns
// (watermark + input guards + devtools detector) so the layout only needs
// a single import. Renders the watermark overlay; everything else is
// side-effects on document/window listeners and html data attributes.
export function SecurityShield({ userEmail, userId, userName }: SecurityShieldProps) {
  useInputGuards();
  useDevtoolsDetector();

  return <WatermarkOverlay email={userEmail} userId={userId} name={userName} />;
}
