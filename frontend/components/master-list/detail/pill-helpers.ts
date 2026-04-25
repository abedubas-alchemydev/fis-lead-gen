import type { PillVariant } from "@/components/ui/pill";

// Backend-enum → <Pill> variant + display label mappings used by the
// firm-detail panels. These mirror the helpers inside
// master-list-workspace-client.tsx (the list view). Duplicated here rather
// than re-exported from the list client because the list client is a
// shared file that the detail page must not modify; co-locating these
// helpers under detail/ keeps the rule simple ("touch only detail/* and
// the detail-page client") without forking shared list-view code.

export function healthVariant(status: string | null): PillVariant {
  if (status === "healthy") return "healthy";
  if (status === "ok") return "ok";
  if (status === "at_risk") return "risk";
  return "unknown";
}

export function healthLabel(status: string | null): string {
  if (status === "healthy") return "Healthy";
  if (status === "ok") return "OK";
  if (status === "at_risk") return "At Risk";
  return "Unknown";
}

export function clearingTypeVariant(value: string | null): PillVariant {
  if (value === "fully_disclosed") return "fd";
  if (value === "self_clearing") return "self";
  if (value === "omnibus") return "omni";
  return "unknown";
}

export function clearingTypeLabel(value: string | null): string {
  if (value === "fully_disclosed") return "Fully Disclosed";
  if (value === "self_clearing") return "Self-Clearing";
  if (value === "omnibus") return "Omnibus";
  return "Unknown";
}

export function priorityVariant(priority: string | null): PillVariant {
  if (priority === "hot") return "hot";
  if (priority === "warm") return "warm";
  if (priority === "cold") return "cold";
  return "unknown";
}

export function priorityLabel(priority: string | null): string {
  if (priority === "hot") return "Hot";
  if (priority === "warm") return "Warm";
  if (priority === "cold") return "Cold";
  return "—";
}

// Replaces the old inline ClassificationBadge component. Returns null when
// the classification is missing/unknown so the caller can skip rendering.
export function classificationDisplay(
  classification: string | null,
): { variant: PillVariant; label: string } | null {
  if (!classification || classification === "unknown") return null;
  if (classification === "true_self_clearing") return { variant: "self", label: "True Self-Clearing" };
  if (classification === "introducing") return { variant: "info", label: "Introducing" };
  return null;
}
