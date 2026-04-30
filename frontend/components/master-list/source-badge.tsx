import { Pill, type PillVariant } from "@/components/ui/pill";
import { SOURCE_BADGE } from "@/lib/format";
import type { ExecutiveSource } from "@/lib/types";

// Maps the format-helper tone (kept independent of the UI layer) onto the
// project's PillVariant set. Amber = warning, Blue = info. Keeping this map
// here means lib/format.ts can stay component-free.
const TONE_TO_VARIANT: Record<"amber" | "blue", PillVariant> = {
  amber: "warning",
  blue: "info",
};

// Renders next to an executive contact's name on the firm detail page.
// SEC-sourced names render no badge (the SEC filing is the authoritative
// source). Apollo-sourced names render an amber "Enriched" pill; FINRA
// fallback names render a blue "FINRA officer" pill. The `title` attribute
// surfaces the longer explanation on hover.
export function SourceBadge({ source }: { source: ExecutiveSource }) {
  const cfg = SOURCE_BADGE[source];
  if (!cfg) return null;
  return (
    <span className="ml-2 inline-flex" title={cfg.tooltip}>
      <Pill variant={TONE_TO_VARIANT[cfg.tone]}>{cfg.label}</Pill>
    </span>
  );
}
