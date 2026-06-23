import { redirect } from "next/navigation";

import { RedoCredibility } from "@/components/landing/redo-credibility";
import { RedoFeatureShowcase } from "@/components/landing/redo-feature-showcase";
import { RedoFilingWire } from "@/components/landing/redo-filing-wire";
import { RedoFinalCta } from "@/components/landing/redo-final-cta";
import { RedoFooter } from "@/components/landing/redo-footer";
import { RedoHeader } from "@/components/landing/redo-header";
import { RedoHero } from "@/components/landing/redo-hero";
import { RedoHowItWorks } from "@/components/landing/redo-how-it-works";
import { RedoMetricsBand } from "@/components/landing/redo-metrics-band";
import { getOptionalSession } from "@/lib/auth-server";

// Public landing page. Stays a SERVER component: it only reads the session to
// bounce authenticated users into the app, then renders the marketing section
// stack. Every section is its own component under components/landing/redo-*;
// interactive pieces are "use client" leaves inside those files. No data
// fetching happens here — sections use local mock fixtures only.
//
// Composition runs dark → light: a dark "command" hero and the streaming
// filing wire up top resolve into bright institutional working surfaces below,
// closing on a dark gradient CTA. See reports/landing-redo-spec.md for the
// full visual system.
export default async function HomePage() {
  const session = await getOptionalSession();
  if (session) redirect("/dashboard");

  return (
    <div className="min-h-screen overflow-x-hidden">
      <RedoHeader />
      <main>
        <RedoHero />
        <RedoFilingWire />
        <RedoMetricsBand />
        <RedoFeatureShowcase />
        <RedoHowItWorks />
        <RedoCredibility />
        <RedoFinalCta />
      </main>
      <RedoFooter />
    </div>
  );
}
