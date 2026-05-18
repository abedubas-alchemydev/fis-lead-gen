import { notFound } from "next/navigation";

import { TopActions } from "@/components/layout/top-actions";
import { VaultFolderDetail } from "@/components/vault/vault-folder-detail";

// /vault/{id} — folder detail page. Shows the folder name + description
// + per-service "Outreach instructions" editor, a drag-drop file
// uploader, and the file list (with status chips, retry, delete).
//
// All data fetching happens client-side in <VaultFolderDetail/> via the
// session cookie, mirroring the rest of the (app) shell. Server
// component just enforces the route param shape + provides the chrome.
export default function VaultFolderDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const folderId = Number.parseInt(params.id, 10);
  if (!Number.isFinite(folderId) || folderId < 1) {
    notFound();
  }

  return (
    <div className="px-7 pb-12 pt-7 lg:px-9">
      <div className="mb-7 flex flex-wrap items-center gap-4">
        <div className="min-w-0">
          <p className="text-[12px] uppercase tracking-[0.06em] text-[var(--text-muted,#94a3b8)]">
            Account <span className="text-[var(--text-dim,#475569)]">/</span>{" "}
            <a
              href="/vault"
              className="transition hover:text-[var(--text-dim,#475569)] hover:underline"
            >
              Vault
            </a>{" "}
            <span className="text-[var(--text-dim,#475569)]">/</span> Service
          </p>
          <h1 className="mt-1 text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
            Service detail
          </h1>
        </div>
        <div className="ml-auto">
          <TopActions />
        </div>
      </div>

      <VaultFolderDetail folderId={folderId} />
    </div>
  );
}
