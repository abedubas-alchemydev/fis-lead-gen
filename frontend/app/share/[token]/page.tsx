import type { Metadata } from "next";
import { Suspense } from "react";

import { ShareClient } from "@/components/share/share-client";
import { PageSpinner } from "@/components/ui/spinner";

// Belt to the robots.ts suspenders: tokenized share URLs must never be
// indexed even if a crawler reaches one through a leaked link.
export const metadata: Metadata = {
  title: "Shared lead list — DOX",
  robots: { index: false, follow: false },
};

// Server component shell. All state (locked/unlocked/revoked) is derived
// client-side from public API fetch outcomes, so the page just unwraps the
// token param and mounts the client. ShareClient uses useSearchParams for
// ?item= routing, which requires a <Suspense> boundary to statically build.
export default async function PublicSharePage(props: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await props.params;

  return (
    <Suspense fallback={<PageSpinner label="Loading shared list" />}>
      <ShareClient token={token} />
    </Suspense>
  );
}
