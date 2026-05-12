import { AdvisorListWorkspaceClient } from "@/components/advisor-list/advisor-list-workspace-client";

// Server wrapper for the Investment Advisor master list. Mirrors
// app/(app)/master-list/page.tsx — all filter/sort/page state lives in
// URL search params and is read inside the client component via
// useSearchParams (lib/advisor-list-state.ts).
export default function AdvisorListPage() {
  return <AdvisorListWorkspaceClient />;
}
