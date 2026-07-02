import { BankDetailClient } from "@/components/banks/bank-detail-client";
import { FeatureAccessDenied } from "@/components/feature-access-denied";
import { getRequiredSession } from "@/lib/auth-server";
import { BANKS, hasFeature } from "@/lib/feature-permissions";

export default async function BankDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  const params = await props.params;
  const session = await getRequiredSession();
  if (!hasFeature(session.user, BANKS)) {
    return <FeatureAccessDenied feature={BANKS} />;
  }
  // `key` forces a fresh remount when the bank id changes so cross-bank
  // navigation cleanly resets the profile state rather than briefly
  // flashing the previous institution's data.
  return <BankDetailClient key={params.id} bankId={params.id} />;
}
