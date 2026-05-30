import { OutreachWorkspaceClient } from "@/components/outreach/outreach-workspace-client";
import { getRequiredSession } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function OutreachSentPage() {
  const session = await getRequiredSession();

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <OutreachWorkspaceClient
        isAdmin={session.user.role === "admin"}
        currentUserId={session.user.id}
      />
    </div>
  );
}
