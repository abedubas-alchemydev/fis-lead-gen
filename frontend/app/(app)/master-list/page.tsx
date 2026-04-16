import { MasterListWorkspaceClient } from "@/components/master-list/master-list-workspace-client";

export default function MasterListPage({
  searchParams
}: {
  searchParams?: {
    clearing_partner?: string;
    clearing_type?: string;
    lead_priority?: string;
    list?: "primary" | "alternative" | "all";
  };
}) {
  return (
    <MasterListWorkspaceClient
      initialClearingPartner={searchParams?.clearing_partner}
      initialClearingType={searchParams?.clearing_type}
      initialLeadPriority={searchParams?.lead_priority}
      initialListMode={searchParams?.list}
    />
  );
}
