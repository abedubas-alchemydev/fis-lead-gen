import { BrandMark } from "@/components/brand/brand-mark";

// Footer — extracted verbatim from the original page.tsx. Stays server-only.
export function SiteFooter() {
  return (
    <footer className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-10">
      <div className="mx-auto flex max-w-7xl flex-col items-center gap-4 px-6 sm:flex-row sm:justify-between">
        <div className="flex items-center gap-2">
          <BrandMark size={28} />
          <span className="text-xs font-semibold text-[var(--text,#0f172a)]">DOX — Institutional Finance Intelligence</span>
        </div>
        <p className="text-xs text-[var(--text-muted,#94a3b8)]">&copy; {new Date().getFullYear()} Alchemy Dev. All rights reserved. Confidential.</p>
      </div>
    </footer>
  );
}
