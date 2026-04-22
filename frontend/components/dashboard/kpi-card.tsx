import Link from "next/link";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";

type Tone = "navy" | "blue" | "danger" | "gold";

// Diagonal tonal gradients give each card a subtle light source without
// pulling it away from its brand color.
const toneMap: Record<Tone, string> = {
  navy: "bg-gradient-to-br from-[#13305a] via-navy to-[#08162c] text-white",
  blue: "bg-gradient-to-br from-[#2477c6] via-blue to-[#144978] text-white",
  danger: "bg-gradient-to-br from-[#ef6d5e] via-danger to-[#c83a2a] text-white",
  gold: "bg-gradient-to-br from-[#f2c26b] via-gold to-[#c98b20] text-navy"
};

const iconChipMap: Record<Tone, string> = {
  navy: "bg-white/10 ring-1 ring-white/15",
  blue: "bg-white/12 ring-1 ring-white/20",
  danger: "bg-white/12 ring-1 ring-white/20",
  gold: "bg-navy/10 ring-1 ring-navy/15"
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
    <article
      className={`relative overflow-hidden rounded-[28px] p-6 shadow-shell ${toneMap[tone]}`}
    >
      {/* Faint radial highlight in the top-right for subtle depth. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-12 -top-12 h-40 w-40 rounded-full bg-white/10 blur-2xl"
      />
      <div className="relative flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-[0.24em] text-current/70">
            {title}
          </p>
          <p className="mt-5 text-5xl font-semibold tabular-nums leading-none">
            {value}
          </p>
          <p className="mt-4 text-sm leading-5 text-current/80">{helper}</p>
        </div>
        <div className={`rounded-2xl p-3 backdrop-blur ${iconChipMap[tone]}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </article>
  );

  if (!href) {
    return content;
  }

  return (
    <Link
      href={href}
      className="block rounded-[28px] transition duration-200 ease-out hover:-translate-y-1 hover:shadow-[0_24px_52px_rgba(10,31,63,0.18)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-2 focus-visible:ring-offset-white/70"
    >
      {content}
    </Link>
  );
}
