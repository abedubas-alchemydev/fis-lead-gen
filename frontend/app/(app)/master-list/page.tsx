import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { MasterListWorkspaceClient } from "@/components/master-list/master-list-workspace-client";
import { getRequiredSession } from "@/lib/auth-server";
import { hasFeature, MASTER_LIST } from "@/lib/feature-permissions";

// All filter / sort / page state is read from URL search params inside
// the client component via useSearchParams (see lib/master-list-state.ts).
// The page wrapper therefore stays tiny — it exists only to satisfy the
// App Router's segment contract.
export default async function MasterListPage() {
  const session = await getRequiredSession();
  if (!hasFeature(session.user, MASTER_LIST)) {
    return <FeatureAccessDenied feature={MASTER_LIST} />;
  }
  return <MasterListWorkspaceClient />;
}
