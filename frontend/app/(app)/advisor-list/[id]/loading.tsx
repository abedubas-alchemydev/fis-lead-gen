// Mirrors the in-component `!profile` fallback inside AdvisorDetailClient
// via the shared DetailPageSkeleton — keeps the route-level Suspense fallback
// visually identical to the in-component fallback so the user never sees a
// spinner→skeleton handoff while navigating to an advisor.
import { DetailPageSkeleton } from "@/components/ui/detail-page-skeleton";

export default function AdvisorDetailLoading() {
  return <DetailPageSkeleton />;
}
