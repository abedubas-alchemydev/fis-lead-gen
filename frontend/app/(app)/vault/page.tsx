import { TopActions } from "@/components/layout/top-actions";
import { VaultClient } from "@/components/vault/vault-client";

export default function VaultPage() {
  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Account <span className="text-[var(--text-dim,#475569)]">/</span> Vault
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Vault
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--text-dim,#475569)]">
            Add the services you sell — name plus a short description. The
            Outreach button on each broker-dealer uses these to compose
            tailored cold-email drafts.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <VaultClient />
    </div>
  );
}
