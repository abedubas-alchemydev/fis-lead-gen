import { Lock } from "lucide-react";

import { TopActions } from "@/components/layout/top-actions";

export default function VaultPage() {
  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-slate-400">
            Account <span className="text-slate-600">/</span> Vault
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-slate-900">
            Vault
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Secure storage for documents and lead artifacts will live here.
          </p>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <div className="flex min-h-[340px] items-center justify-center rounded-[30px] border border-white/80 bg-white/88 p-10 shadow-shell backdrop-blur">
        <div className="flex flex-col items-center text-center">
          <div className="grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-slate-500">
            <Lock className="h-6 w-6" strokeWidth={1.75} aria-hidden />
          </div>
          <h2 className="mt-5 text-lg font-semibold text-navy">Coming soon</h2>
          <p className="mt-2 max-w-sm text-sm text-slate-600">
            Encrypted document storage is on the roadmap — nothing to stash yet.
          </p>
        </div>
      </div>
    </div>
  );
}
