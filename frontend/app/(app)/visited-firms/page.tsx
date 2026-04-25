import { TopActions } from "@/components/layout/top-actions";
import { VisitedFirmsClient } from "@/components/visited-firms/visited-firms-client";

export default function VisitedFirmsPage() {
  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Enterprise Dashboard{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Visited Firms
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Visited Firms
          </h1>
          <p className="mt-2 max-w-2xl text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Firms you&apos;ve recently viewed, newest first, so you can pick back up where you left off.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <VisitedFirmsClient />
    </div>
  );
}
