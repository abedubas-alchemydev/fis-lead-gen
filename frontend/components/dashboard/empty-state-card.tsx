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
    <article className="rounded-[30px] border border-white/80 bg-white/88 p-7 shadow-shell backdrop-blur">
      <p className="text-sm font-medium uppercase tracking-[0.24em] text-blue">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold text-navy">{title}</h2>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">{description}</p>
      <div className="mt-8 grid gap-3 md:grid-cols-2">
        <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-700">{bulletOne}</div>
        <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-700">{bulletTwo}</div>
      </div>
    </article>
  );
}

