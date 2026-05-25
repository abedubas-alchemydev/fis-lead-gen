import type { ReactNode } from "react";

import { ChatbotWidget } from "@/components/chatbot/chatbot-widget";
import { AppShell } from "@/components/layout/app-shell";
import { SecurityShield } from "@/components/security/security-shield";
import { Toaster } from "@/components/ui/toaster";
import { ActivityProvider } from "@/lib/activity/provider";
import { getRequiredSession } from "@/lib/auth-server";

export default async function ProtectedAppLayout({ children }: { children: ReactNode }) {
  const session = await getRequiredSession();

  return (
    <>
      <SecurityShield
        userEmail={session.user.email}
        userId={session.user.id}
        userName={session.user.name ?? undefined}
      />
      <ActivityProvider>
        <AppShell session={session}>{children}</AppShell>
      </ActivityProvider>
      <Toaster />
      <ChatbotWidget />
    </>
  );
}
