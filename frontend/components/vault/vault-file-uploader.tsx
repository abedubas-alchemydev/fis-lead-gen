"use client";

import { useCallback, useRef, useState } from "react";

import { ApiError, uploadVaultFile } from "@/lib/api";
import type { VaultFolderFile } from "@/lib/types";

// Drag-drop + click-to-pick uploader. Handles one file at a time
// because each upload is a separate multipart POST (the BE doesn't
// batch). Multi-file selection iterates sequentially so the BE-side
// folder-count cap is enforced predictably without race conditions.

interface VaultFileUploaderProps {
  folderId?: number;
  disabled?: boolean;
  onUploaded?: (file: VaultFolderFile) => void;
  // When provided, selected files are handed back to the parent to stage
  // (no upload). Used by the Vault create form, which has no folder id to
  // POST to until the service row exists. When omitted, files upload
  // immediately to ``folderId`` — the detail-page behavior.
  onStage?: (files: File[]) => void;
}

const ACCEPT_LIST = [
  "application/pdf",
  ".pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".docx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".pptx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".xlsx",
  "text/plain",
  ".txt",
  "text/markdown",
  ".md",
  "text/html",
  ".html",
  ".htm",
  "text/csv",
  ".csv",
  "application/rtf",
  ".rtf",
  "application/json",
  ".json"
].join(",");

export function VaultFileUploader({
  folderId,
  disabled = false,
  onUploaded,
  onStage
}: VaultFileUploaderProps) {
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || disabled) return;
      // Stage mode (create form): hand the File objects back without
      // uploading — the parent uploads them after the folder is created.
      if (onStage) {
        onStage(Array.from(files));
        if (inputRef.current) inputRef.current.value = "";
        return;
      }
      // Upload mode (detail page) requires an existing folder.
      if (folderId === undefined) return;
      setError(null);
      setBusy(true);
      try {
        for (const file of Array.from(files)) {
          try {
            const uploaded = await uploadVaultFile(folderId, file);
            onUploaded?.(uploaded);
          } catch (err) {
            // Show the first failure inline; keep going with the rest
            // so a single bad file doesn't tank an otherwise-OK batch.
            setError(buildUploadErrorMessage(file.name, err));
          }
        }
      } finally {
        setBusy(false);
        if (inputRef.current) inputRef.current.value = "";
      }
    },
    [folderId, disabled, onUploaded, onStage]
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      event.stopPropagation();
      setOver(false);
      void handleFiles(event.dataTransfer?.files ?? null);
    },
    [handleFiles]
  );

  const onDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!disabled) setOver(true);
  }, [disabled]);

  const onDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setOver(false);
  }, []);

  return (
    <div>
      <div
        onClick={() => {
          if (!disabled && !busy) inputRef.current?.click();
        }}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!disabled && !busy) inputRef.current?.click();
          }
        }}
        className={[
          "flex min-h-[120px] cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-6 text-center transition",
          disabled
            ? "cursor-not-allowed border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] text-[var(--text-muted,#94a3b8)]"
            : over
              ? "border-[var(--accent,#6366f1)] bg-[var(--accent,#6366f1)]/10 text-[var(--text,#0f172a)]"
              : "border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] hover:border-[var(--accent,#6366f1)] hover:bg-[var(--surface-2,#f1f6fd)]"
        ].join(" ")}
      >
        <p className="text-sm font-medium">
          {busy
            ? "Uploading..."
            : disabled
              ? "Folder is at the 20-file limit"
              : "Drop files here or click to browse"}
        </p>
        <p className="mt-1 text-[11px] text-[var(--text-muted,#94a3b8)]">
          PDF, DOCX, PPTX, XLSX, TXT, MD, RTF, CSV, HTML, JSON · up to 10 MB each
        </p>
      </div>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_LIST}
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />

      {error ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}
    </div>
  );
}

function buildUploadErrorMessage(filename: string, error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 415) {
      return `${filename}: file type not supported.`;
    }
    if (error.status === 413) {
      return `${filename}: too large (10 MB max).`;
    }
    if (error.status === 409) {
      return error.detail; // BE message already explains storage / file-count caps
    }
    if (error.status === 503) {
      return `${filename}: file storage isn't configured. Contact an administrator.`;
    }
    return `${filename}: ${error.detail}`;
  }
  if (error instanceof Error) {
    return `${filename}: ${error.message}`;
  }
  return `${filename}: upload failed.`;
}
