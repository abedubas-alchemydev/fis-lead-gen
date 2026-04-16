import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { auth } from "@/lib/auth";

export async function getOptionalSession() {
  return auth.api.getSession({
    headers: headers()
  });
}

export async function getRequiredSession() {
  const session = await getOptionalSession();

  if (!session) {
    redirect("/login");
  }

  return session;
}
