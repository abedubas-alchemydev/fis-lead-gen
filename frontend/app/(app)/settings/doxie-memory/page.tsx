import { DoxieMemoryClient } from "@/components/settings/doxie-memory-client";
import { getRequiredSession } from "@/lib/auth-server";

export default async function DoxieMemoryPage() {
  // Per-user page — every authenticated user manages their own memory. No
  // admin gate (unlike /settings/users).
  await getRequiredSession();

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      <header className="mb-7 max-w-2xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          Doxie
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          What Doxie remembers about you
        </h1>
        <p className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          When you tell Doxie a durable fact or preference &mdash; the firms you focus on, your standard fee,
          how you like outreach drafted &mdash; it saves a note here and folds it into every future chat. These
          memories are private to you. Delete any you no longer want Doxie to use.
        </p>
      </header>

      <DoxieMemoryClient />
    </div>
  );
}
