"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  deleteVaultFile,
  deleteVaultFolder,
  listVaultFiles,
  listVaultFolders,
  retryVaultFile,
  updateVaultFolder
} from "@/lib/api";
import type { VaultFolder, VaultFolderFile } from "@/lib/types";

import { VaultFileRow } from "./vault-file-row";
import { VaultFileUploader } from "./vault-file-uploader";
import { VaultInstructionsEditor } from "./vault-instructions-editor";

// Detail panel for a single Vault service. Three editable surfaces:
// the description, the per-service outreach instructions (Deshorn's ask),
// and the attached file list. Files are uploaded async — the uploader
// hands rows back to this component which appends + polls them.
//
// Polling: we re-fetch the file list every 2s while ANY row is in
// extracting / embedding state, and stop once everything is terminal
// (ready / failed). Saves the FE from per-row polling churn.

const POLL_INTERVAL_MS = 2_000;
const NAME_MAX = 255;
const DESCRIPTION_MAX = 20_000;
const INSTRUCTIONS_MAX = 10_000;

export function VaultFolderDetail({ folderId }: { folderId: number }) {
  const [folder, setFolder] = useState<VaultFolder | null>(null);
  const [files, setFiles] = useState<VaultFolderFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const refreshFiles = useCallback(async () => {
    try {
      const result = await listVaultFiles(folderId);
      if (!isMountedRef.current) return;
      setFiles(result);
    } catch (err) {
      if (!isMountedRef.current) return;
      // Don't replace folder-level error — file-list errors are
      // recoverable; surface a row-level retry instead. Logging only.
      console.error("vault file list:", err);
    }
  }, [folderId]);

  // Initial load: folder metadata via listVaultFolders + filter (no
  // dedicated GET /{id} endpoint in v1 — the listing is small and
  // already authorized to the caller).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [folders, fileList] = await Promise.all([
          listVaultFolders(),
          listVaultFiles(folderId)
        ]);
        if (cancelled || !isMountedRef.current) return;
        const match = folders.find((f) => f.id === folderId);
        if (!match) {
          setLoadError("Service not found.");
          return;
        }
        setFolder(match);
        setFiles(fileList);
      } catch (err) {
        if (cancelled || !isMountedRef.current) return;
        if (err instanceof ApiError && err.status === 404) {
          setLoadError("Service not found.");
        } else {
          setLoadError(errorMessage(err));
        }
      } finally {
        if (!cancelled && isMountedRef.current) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [folderId]);

  // Polling: tick while any file row is non-terminal.
  useEffect(() => {
    const hasInFlight = files.some(
      (f) => f.processing_status === "extracting" || f.processing_status === "embedding"
    );
    if (!hasInFlight) return;
    const interval = window.setInterval(() => {
      void refreshFiles();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [files, refreshFiles]);

  function handleUploaded(file: VaultFolderFile) {
    setFiles((current) => [file, ...current.filter((f) => f.id !== file.id)]);
  }

  async function handleRetry(fileId: number) {
    try {
      const updated = await retryVaultFile(folderId, fileId);
      setFiles((current) =>
        current.map((f) => (f.id === fileId ? updated : f))
      );
    } catch (err) {
      console.error("vault file retry:", err);
    }
  }

  async function handleDelete(fileId: number) {
    try {
      await deleteVaultFile(folderId, fileId);
      setFiles((current) => current.filter((f) => f.id !== fileId));
    } catch (err) {
      console.error("vault file delete:", err);
    }
  }

  if (loading) {
    return (
      <div className="rounded-[30px] border border-white/80 bg-white/88 p-10 text-sm text-slate-500 shadow-shell backdrop-blur">
        Loading service...
      </div>
    );
  }

  if (loadError || !folder) {
    return (
      <div className="rounded-[30px] border border-red-200 bg-red-50/80 p-6 text-sm text-danger shadow-shell">
        {loadError ?? "Could not load this service."}
        <p className="mt-3">
          <a href="/vault" className="font-medium text-blue underline-offset-4 hover:underline">
            Back to Vault
          </a>
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <FolderEditor
        folder={folder}
        onSaved={(updated) => setFolder(updated)}
        onDeleted={() => {
          if (typeof window !== "undefined") {
            window.location.assign("/vault");
          }
        }}
      />

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <header className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-navy">Reference files</h2>
          <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">
            {files.length} / 20
          </p>
        </header>
        <p className="mt-1 text-xs leading-5 text-slate-500">
          Uploaded files are extracted, chunked, and embedded so the Outreach
          AI can pull the most relevant passages into each draft.
          PDF, DOCX, PPTX, XLSX, TXT, MD, RTF, CSV, HTML, JSON. Up to 10 MB
          each.
        </p>

        <div className="mt-4">
          <VaultFileUploader
            folderId={folderId}
            disabled={files.length >= 20}
            onUploaded={handleUploaded}
          />
        </div>

        {files.length === 0 ? (
          <p className="mt-5 text-xs italic text-slate-400">
            No files attached yet — drafts will fall back to the description
            + instructions only.
          </p>
        ) : (
          <ul className="mt-5 space-y-2">
            {files.map((file) => (
              <li key={file.id}>
                <VaultFileRow
                  folderId={folderId}
                  file={file}
                  onRetry={() => void handleRetry(file.id)}
                  onDelete={() => void handleDelete(file.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function FolderEditor({
  folder,
  onSaved,
  onDeleted
}: {
  folder: VaultFolder;
  onSaved: (folder: VaultFolder) => void;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(folder.name);
  const [description, setDescription] = useState(folder.description);
  const [instructions, setInstructions] = useState(folder.outreach_instructions);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Reset local edits if the parent re-loaded the folder (e.g. after
  // a successful save round-trips fresh server values).
  useEffect(() => {
    setName(folder.name);
    setDescription(folder.description);
    setInstructions(folder.outreach_instructions);
  }, [folder.id, folder.name, folder.description, folder.outreach_instructions]);

  const dirty =
    name !== folder.name ||
    description !== folder.description ||
    instructions !== folder.outreach_instructions;

  async function handleSave() {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateVaultFolder(folder.id, {
        name: name.trim(),
        description,
        outreach_instructions: instructions
      });
      onSaved(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (deleting) return;
    setDeleting(true);
    try {
      await deleteVaultFolder(folder.id);
      onDeleted();
    } catch (err) {
      setError(errorMessage(err));
      setDeleting(false);
    }
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="space-y-4">
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
          Service name
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX}
            className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20"
          />
        </label>
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={DESCRIPTION_MAX}
            rows={5}
            placeholder="What you offer, your differentiators, typical client profile..."
            className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20"
          />
          <span className="mt-1 block text-[11px] text-slate-400">
            {description.length.toLocaleString()} / {DESCRIPTION_MAX.toLocaleString()}{" "}
            characters
          </span>
        </label>
        <VaultInstructionsEditor
          value={instructions}
          onChange={setInstructions}
          maxLength={INSTRUCTIONS_MAX}
        />
      </div>

      {error ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        {confirmDelete ? (
          <div className="flex items-center gap-3 text-xs text-danger">
            <span>Delete this service and all its files?</span>
            <button
              type="button"
              onClick={() => setConfirmDelete(false)}
              disabled={deleting}
              className="font-medium text-slate-600 underline-offset-4 hover:underline"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={deleting}
              className="inline-flex h-8 items-center rounded-lg bg-danger px-3 text-xs font-semibold text-white transition hover:bg-[#c62a2a] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {deleting ? "Deleting..." : "Delete service"}
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="text-xs font-medium text-slate-500 underline-offset-4 transition hover:text-danger hover:underline"
          >
            Delete service
          </button>
        )}

        <div className="flex items-center gap-3">
          {savedAt && !dirty ? (
            <span className="text-xs text-emerald-600">Saved.</span>
          ) : null}
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={!dirty || saving || !name.trim()}
            className="inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? "Saving..." : "Save changes"}
          </button>
        </div>
      </div>
    </section>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
