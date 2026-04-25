import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { Toaster } from "@/components/ui/toaster";
import { getRequiredSession } from "@/lib/auth-server";

export default async function ProtectedAppLayout({ children }: { children: ReactNode }) {
  const session = await getRequiredSession();

  return (
    <>
      <AppShell session={session}>{children}</AppShell>
      <Toaster />
    </>
  );
}
