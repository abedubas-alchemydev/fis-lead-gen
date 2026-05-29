"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";

import { fetchInstitutionalInvestorProfile } from "@/lib/api";

// The standalone Institutional Investors page was merged into the Investment
// Advisors list (every 13F filer in our data is also an RIA — surfaced via the
// "13F" column there). This stub keeps old `/institutional-investors/<id>`
// links and Doxie `ii_detail_url` answers working by resolving the investor to
// its linked advisor and replacing the URL. The id space differs between the
// two tables, so we must look up `advisor_id`; we fall back to the advisor list
// if the investor can't be resolved (404/403/no link).
export function InvestorRedirectClient({ investorId }: { investorId: string }) {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let target = "/advisor-list";
      try {
        const profile = await fetchInstitutionalInvestorProfile(investorId);
        if (profile.investor.advisor_id != null) {
          target = `/advisor-list/${profile.investor.advisor_id}`;
        }
      } catch {
        // Unresolved — land on the advisor list rather than dead-end.
      }
      if (!cancelled) router.replace(target as Route);
    })();
    return () => {
      cancelled = true;
    };
  }, [investorId, router]);

  return (
    <div className="flex min-h-[40vh] items-center justify-center text-[13px] text-[var(--text-muted,#94a3b8)]">
      Redirecting to the investment advisor…
    </div>
  );
}
