"use client";

const toneMap: Record<string, { label: string; stars: string; className: string }> = {
  hot: { label: "Hot", stars: "***", className: "bg-amber-100 text-amber-700" },
  warm: { label: "Warm", stars: "**", className: "bg-blue-100 text-blue" },
  cold: { label: "Cold", stars: "*", className: "bg-slate-100 text-slate-600" }
};

export function LeadPriorityBadge({
  priority,
  score
}: {
  priority: string | null;
  score: number | null;
}) {
  if (!priority) {
    return <span className="text-sm text-slate-400">Not scored</span>;
  }

  const tone = toneMap[priority] ?? toneMap.cold;
  return (
    <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ${tone.className}`}>
      <span>{tone.label}</span>
      <span className="font-mono">{tone.stars}</span>
      {score !== null ? <span>{score.toFixed(0)}</span> : null}
    </span>
  );
}
