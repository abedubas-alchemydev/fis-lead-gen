import { redirect } from "next/navigation";

import { FeatureTour } from "@/components/landing/feature-tour";
import { FilingFeedTicker } from "@/components/landing/filing-feed-ticker";
import { FinalCta } from "@/components/landing/final-cta";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { StatStrip } from "@/components/landing/stat-strip";
import { getOptionalSession } from "@/lib/auth-server";

export default async function HomePage() {
  const session = await getOptionalSession();
  if (session) redirect("/dashboard");

  return (
    <div className="min-h-screen">
      <SiteHeader />
      <Hero />
      <FilingFeedTicker />
      <StatStrip />
      <FeatureTour />
      <HowItWorks />
      <FinalCta />
      <SiteFooter />
    </div>
  );
}
