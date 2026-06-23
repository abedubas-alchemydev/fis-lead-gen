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

  // The landing is a fixed dark → light → dark marketing design whose mid
  // sections render their copy with the shared --text/--surface tokens on a
  // light canvas. Those tokens flip to the dark palette under the app-wide
  // theme switch (layout.tsx stamps <html data-theme="dark"> from the user's
  // saved `prospectEngineTheme`), which would turn the light sections' text
  // near-white-on-light — invisible. Pin the whole public landing to its
  // designed LIGHT theme so it reads correctly regardless of the saved app
  // theme; the [data-theme="light"] scope (globals.css) re-asserts the light
  // palette, and the opaque --bg fill guarantees no dark canvas bleeds through
  // the translucent section backgrounds. The dark hero + dark CTA paint their
  // own hardcoded navy/gradient fills, so they stay dark as designed.
  return (
    <div data-theme="light" className="min-h-screen overflow-x-hidden bg-[var(--bg,#eaf3ff)]">
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
