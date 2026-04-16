import { AlertsClient } from "@/components/alerts/alerts-client";

export default function AlertsPage({
  searchParams
}: {
  searchParams?: { form_type?: string; priority?: string };
}) {
  return <AlertsClient initialFormType={searchParams?.form_type} initialPriority={searchParams?.priority} />;
}
