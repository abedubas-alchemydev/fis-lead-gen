import { InvestorRedirectClient } from "@/components/institutional-investors/investor-redirect-client";

// The Institutional Investors feature was merged into the Investment Advisors
// list. This detail route now resolves the investor to its linked advisor and
// forwards there, keeping old `/institutional-investors/<id>` links and Doxie
// `ii_detail_url` answers working. Permission is enforced on the advisor detail
// page we land on.
export default async function InstitutionalInvestorDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await props.params;
  return <InvestorRedirectClient investorId={id} />;
}
