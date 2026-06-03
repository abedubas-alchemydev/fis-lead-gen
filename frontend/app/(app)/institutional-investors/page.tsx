import { redirect } from "next/navigation";

// The standalone Institutional Investors list was merged into the Investment
// Advisors list: in our data every 13F filer is also a registered RIA, so the
// advisor list (which defaults to "13F filers only" and flags each filer via
// the "13F" column) is the single source of truth. This route is kept as a
// redirect so old bookmarks and Doxie `ii_list_url` answers still resolve.
// The advisor list enforces its own feature permission.
export default function InstitutionalInvestorsListPage() {
  redirect("/advisor-list");
}
