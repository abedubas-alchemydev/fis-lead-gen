import { MasterListWorkspaceClient } from "@/components/master-list/master-list-workspace-client";

// All filter / sort / page state is read from URL search params inside
// the client component via useSearchParams (see lib/master-list-state.ts).
// The page wrapper therefore stays tiny — it exists only to satisfy the
// App Router's segment contract.
export default function MasterListPage() {
  return <MasterListWorkspaceClient />;
}
