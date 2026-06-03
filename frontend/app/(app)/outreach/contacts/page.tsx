import { OutreachContactsClient } from "@/components/outreach-contacts/outreach-contacts-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function OutreachContactsPage() {
  await getRequiredSession();

  return <OutreachContactsClient />;
}
