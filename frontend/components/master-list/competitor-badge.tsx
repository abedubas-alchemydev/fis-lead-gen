export function CompetitorBadge({ isCompetitor }: { isCompetitor: boolean }) {
  if (!isCompetitor) {
    return null;
  }

  return (
    <span className="inline-flex rounded-full border border-danger/20 bg-danger/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-danger">
      Competitor
    </span>
  );
}
