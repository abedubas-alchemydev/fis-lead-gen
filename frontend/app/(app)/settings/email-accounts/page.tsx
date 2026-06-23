import { EmailAccountsClient } from "@/components/settings/email-accounts-client";
import { getRequiredSession } from "@/lib/auth-server";

export default async function EmailAccountsPage() {
  // Gates anonymous visitors; the per-account multi-sender feature
  // applies to every authenticated role, no admin-gate here.
  await getRequiredSession();

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      <EmailAccountsClient />
    </div>
  );
}
