"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getVaultFileDownloadPath } from "@/lib/api";
import type { VaultFolderFile, VaultFolderFileStatus } from "@/lib/types";

// One row per uploaded file. Shows status chip, filename, byte size,
// download/retry/delete actions. The parent owns the file list state
// and polls non-terminal rows; this component is purely presentational
// + emits the user's action intent via callbacks.

interface VaultFileRowProps {
  folderId: number;
  file: VaultFolderFile;
  onRetry: () => void;
  onDelete: () => void;
}

export function VaultFileRow({ folderId, file, onRetry, onDelete }: VaultFileRowProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const downloadPath = getVaultFileDownloadPath(folderId, file.id);
  const status = file.processing_status;

  return (
    <article className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <StatusChip status={status} />
          <a
            href={downloadPath}
            target="_blank"
            rel="noreferrer"
            className="truncate text-sm font-medium text-[var(--text,#0f172a)] underline-offset-4 hover:underline"
            title={file.original_filename}
          >
            {file.original_filename}
          </a>
        </div>
        <p className="mt-1 text-[11px] text-[var(--text-muted,#94a3b8)]">
          {formatBytes(file.size_bytes)} · uploaded {formatRelative(file.created_at)}
        </p>
        {status === "failed" && file.processing_error ? (
          <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] leading-4 text-amber-900">
            {file.processing_error}
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {status === "failed" ? (
          <button
            type="button"
            onClick={onRetry}
            className="text-xs font-medium text-[var(--accent,#6366f1)] underline-offset-4 transition hover:underline"
          >
            Retry
          </button>
        ) : null}
        {confirmingDelete ? (
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setConfirmingDelete(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              size="sm"
              onClick={() => {
                setConfirmingDelete(false);
                onDelete();
              }}
            >
              Delete
            </Button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            className="text-xs font-medium text-[var(--text-muted,#94a3b8)] underline-offset-4 transition hover:text-danger hover:underline"
          >
            Delete
          </button>
        )}
      </div>
    </article>
  );
}

function StatusChip({ status }: { status: VaultFolderFileStatus }) {
  const palette = STATUS_PALETTE[status];
  return (
    <span
      className={[
        "inline-flex h-5 items-center gap-1 rounded-full px-2 text-[10px] font-medium uppercase tracking-[0.08em]",
        palette
      ].join(" ")}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

const STATUS_LABEL: Record<VaultFolderFileStatus, string> = {
  extracting: "Reading...",
  embedding: "Indexing...",
  ready: "Ready",
  failed: "Couldn't read"
};

const STATUS_PALETTE: Record<VaultFolderFileStatus, string> = {
  extracting: "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]",
  embedding: "bg-[var(--surface-3,#dbeafe)] text-[var(--text-dim,#475569)]",
  ready: "bg-emerald-100 text-emerald-700",
  failed: "bg-amber-100 text-amber-800"
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const diffMs = Date.now() - then;
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
