import { MasterListWorkspaceClient } from "@/components/master-list/master-list-workspace-client";

// Normalizes Next.js multi-value search params (`?k=a&k=b`) and CSV
// (`?k=a,b`) into a plain `string[]`. Mirrors backend `_parse_states` so a
// link copy/paste from the BE behaves identically on the FE.
function parseMultiParam(raw: string | string[] | undefined): string[] {
  if (raw === undefined) return [];
  const items = Array.isArray(raw) ? raw : [raw];
  return items
    .flatMap((item) => item.split(","))
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

export default function MasterListPage({
  searchParams
}: {
  searchParams?: {
    clearing_partner?: string;
    clearing_type?: string;
    lead_priority?: string;
    list?: "primary" | "alternative" | "all";
    types_of_business?: string | string[];
  };
}) {
  return (
    <MasterListWorkspaceClient
      initialClearingPartner={searchParams?.clearing_partner}
      initialClearingType={searchParams?.clearing_type}
      initialLeadPriority={searchParams?.lead_priority}
      initialListMode={searchParams?.list}
      initialTypesOfBusiness={parseMultiParam(searchParams?.types_of_business)}
    />
  );
}
