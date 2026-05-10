const toneMap: Record<string, string> = {
  self_clearing: "border-navy/20 bg-navy text-white",
  fully_disclosed: "border-blue/20 bg-blue text-white",
  omnibus: "border-slate-200 bg-slate-200 text-slate-700",
  unknown: "border-dashed border-slate-300 bg-white text-slate-500"
};

const labelMap: Record<string, string> = {
  self_clearing: "Self-Clearing",
  fully_disclosed: "Fully Disclosed",
  omnibus: "Omnibus",
  unknown: "Unknown"
};

export function ClearingTypeBadge({ type }: { type: string | null }) {
  const normalized = type ?? "unknown";
  return (
    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-medium ${toneMap[normalized] ?? toneMap.unknown}`}>
      {labelMap[normalized] ?? labelMap.unknown}
    </span>
  );
}
