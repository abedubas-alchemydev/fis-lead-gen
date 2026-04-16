import Link from "next/link";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";

type Tone = "navy" | "blue" | "danger" | "gold";

const toneMap: Record<Tone, string> = {
  navy: "bg-navy text-white",
  blue: "bg-blue text-white",
  danger: "bg-danger text-white",
  gold: "bg-gold text-navy"
};

export function KpiCard({
  title,
  value,
  helper,
  tone,
  icon: Icon,
  href
}: {
  title: string;
  value: string;
  helper: string;
  tone: Tone;
  icon: LucideIcon;
  href?: Route;
}) {
  const content = (
    <article className={`rounded-[28px] p-6 shadow-shell ${toneMap[tone]}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-current/70">{title}</p>
          <p className="mt-6 text-4xl font-semibold">{value}</p>
          <p className="mt-4 text-sm text-current/80">{helper}</p>
        </div>
        <div className="rounded-2xl bg-white/15 p-3 text-current">
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </article>
  );

  if (!href) {
    return content;
  }

  return (
    <Link href={href} className="block transition hover:-translate-y-0.5">
      {content}
    </Link>
  );
}
