const toneMap: Record<string, string> = {
  healthy: "bg-success/15 text-success border-success/20",
  ok: "bg-warning/15 text-warning border-warning/20",
  at_risk: "bg-danger/15 text-danger border-danger/20",
  unknown: "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)] border-[var(--border,rgba(30,64,175,0.1))]"
};

const labelMap: Record<string, string> = {
  healthy: "Healthy",
  ok: "OK",
  at_risk: "At Risk",
  unknown: "Not assessed"
};

export function HealthBadge({ status }: { status: string | null }) {
  const normalized = status ?? "unknown";
  return (
    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-medium ${toneMap[normalized] ?? toneMap.unknown}`}>
      {labelMap[normalized] ?? labelMap.unknown}
    </span>
  );
}
