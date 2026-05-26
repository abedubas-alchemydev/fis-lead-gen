import { AlertsClient } from "@/components/alerts/alerts-client";

export default async function AlertsPage(
  props: {
    searchParams?: Promise<{ form_type?: string; priority?: string }>;
  }
) {
  const searchParams = await props.searchParams;
  return <AlertsClient initialFormType={searchParams?.form_type} initialPriority={searchParams?.priority} />;
}
