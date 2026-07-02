import { BankListWorkspaceClient } from "@/components/banks/bank-list-workspace-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { BANKS, hasFeature } from "@/lib/feature-permissions";

// Server wrapper for the bank-charter list. Mirrors
// app/(app)/master-list/page.tsx — all filter/sort/page state lives in
// URL search params and is read inside the client component via
// useSearchParams (lib/bank-list-state.ts).
export default async function BanksPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, BANKS)) {
    return <FeatureAccessDenied feature={BANKS} />;
  }
  return <BankListWorkspaceClient />;
}
