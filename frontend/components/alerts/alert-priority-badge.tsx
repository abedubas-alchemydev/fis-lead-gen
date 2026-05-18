"use client";

const toneMap: Record<string, string> = {
  critical: "bg-red-100 text-danger",
  high: "bg-amber-100 text-amber-700",
  medium: "bg-blue-100 text-[var(--blue,#3b82f6)]",
  low: "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]"
};

export function AlertPriorityBadge({ priority }: { priority: string }) {
  return (
    <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium capitalize ${toneMap[priority] ?? toneMap.low}`}>
      {priority.replaceAll("_", " ")}
    </span>
  );
}
