// Mirrors the in-component `bank === null` fallback inside BankDetailClient
// via the shared DetailPageSkeleton — keeps the route-level Suspense fallback
// visually identical to the in-component fallback so the user never sees a
// spinner→skeleton handoff while navigating to a bank.
import { DetailPageSkeleton } from "@/components/ui/detail-page-skeleton";

export default function BankDetailLoading() {
  return <DetailPageSkeleton />;
}
