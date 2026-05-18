export function EmptyStateCard({
  eyebrow,
  title,
  description,
  bulletOne,
  bulletTwo
}: {
  eyebrow: string;
  title: string;
  description: string;
  bulletOne: string;
  bulletTwo: string;
}) {
  return (
    <article className="rounded-[30px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/95 p-7 shadow-shell backdrop-blur">
      <p className="text-sm font-medium uppercase tracking-[0.24em] text-[var(--accent,#6366f1)]">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold text-[var(--text,#0f172a)]">{title}</h2>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-[var(--text-dim,#475569)]">{description}</p>
      <div className="mt-8 grid gap-3 md:grid-cols-2">
        <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] p-4 text-sm text-[var(--text-dim,#475569)]">{bulletOne}</div>
        <div className="rounded-2xl bg-[var(--surface-2,#f1f6fd)] p-4 text-sm text-[var(--text-dim,#475569)]">{bulletTwo}</div>
      </div>
    </article>
  );
}

