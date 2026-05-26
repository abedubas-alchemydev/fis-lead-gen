import { AdvisorListWorkspaceClient } from "@/components/advisor-list/advisor-list-workspace-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, INVESTMENT_ADVISORS } from "@/lib/feature-permissions";

// Server wrapper for the Investment Advisor master list. Mirrors
// app/(app)/master-list/page.tsx — all filter/sort/page state lives in
// URL search params and is read inside the client component via
// useSearchParams (lib/advisor-list-state.ts).
export default async function AdvisorListPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, INVESTMENT_ADVISORS)) {
    return <FeatureAccessDenied feature={INVESTMENT_ADVISORS} />;
  }
  return <AdvisorListWorkspaceClient />;
}
