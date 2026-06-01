const toneMap: Record<string, string> = {
  self_clearing: "border-[var(--accent,#6366f1)]/20 bg-[var(--accent,#6366f1)] text-white",
  fully_disclosed: "border-[var(--blue,#3b82f6)]/20 bg-[var(--blue,#3b82f6)] text-white",
  omnibus: "border-[var(--surface-3,#dbeafe)] bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]",
  non_carrying: "border-[var(--slate,#64748b)]/20 bg-[var(--slate,#64748b)] text-white",
  unknown: "border-dashed border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-muted,#94a3b8)]"
};

const labelMap: Record<string, string> = {
  self_clearing: "Self-Clearing",
  fully_disclosed: "Fully Disclosed",
  omnibus: "Omnibus",
  non_carrying: "Non-Carrying",
  unknown: "Not classified"
};

export function ClearingTypeBadge({ type }: { type: string | null }) {
  const normalized = type ?? "unknown";
  return (
    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-medium ${toneMap[normalized] ?? toneMap.unknown}`}>
      {labelMap[normalized] ?? labelMap.unknown}
    </span>
  );
}
