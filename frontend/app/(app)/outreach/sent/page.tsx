import { OutreachSentClient } from "@/components/outreach/outreach-sent-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function OutreachSentPage() {
  const session = await getRequiredSession();

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <OutreachSentClient isAdmin={session.user.role === "admin"} />
    </div>
  );
}
