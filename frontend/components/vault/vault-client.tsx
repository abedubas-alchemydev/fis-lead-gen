"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { X } from "lucide-react";

import {
  ApiError,
  createVaultFolder,
  deleteVaultFolder,
  listVaultFolders,
  updateVaultFolder,
  uploadVaultFile
} from "@/lib/api";
import type { VaultFolder, VaultFolderCreate } from "@/lib/types";

import { DefaultSenderSelect } from "./default-sender-select";
import { VaultFileUploader } from "./vault-file-uploader";
import { VaultInstructionsEditor } from "./vault-instructions-editor";

// Vault page body — folder CRUD UI for the per-user services that back the
// /master-list/{id} Outreach modal. Each folder is a service name plus a
// freeform description; the description text is fed verbatim to Gemini
// when the user clicks "Generate draft".
//
// The create/edit form (FolderForm) collects EVERYTHING a service needs in
// one shot — name, description, outreach instructions, default sender, and
// (create-only) reference files — so the user no longer has to create a
// service and then re-open it to finish setup. Files can only be POSTed once
// the folder row exists, so on create they're staged in the browser and
// uploaded sequentially right after the folder is created.
//
// Layout: empty-state card when the user has nothing yet, otherwise a
// grid of cards with inline edit-in-place + delete-confirm.

const NAME_MAX = 255;
const DESCRIPTION_MAX = 20_000;
const INSTRUCTIONS_MAX = 10_000;
const MAX_FILES = 20;

type EditState =
  | { mode: "idle" }
  | { mode: "create" }
  | { mode: "edit"; folderId: number }
  | { mode: "delete"; folderId: number };

export function VaultClient() {
  const [loading, setLoading] = useState(true);
  const [folders, setFolders] = useState<VaultFolder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editState, setEditState] = useState<EditState>({ mode: "idle" });
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await listVaultFolders();
        if (cancelled || !isMountedRef.current) return;
        setFolders(result);
      } catch (err) {
        if (cancelled || !isMountedRef.current) return;
        setError(errorMessage(err));
      } finally {
        if (!cancelled && isMountedRef.current) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Returns the created folder so FolderForm can upload staged files to its
  // id. Does NOT close the form — FolderForm closes itself after any file
  // uploads finish so the user sees upload progress.
  async function handleCreate(payload: VaultFolderCreate): Promise<VaultFolder> {
    const created = await createVaultFolder(payload);
    if (isMountedRef.current) {
      setFolders((current) => [created, ...current]);
    }
    return created;
  }

  async function handleUpdate(
    folderId: number,
    payload: VaultFolderCreate
  ): Promise<VaultFolder> {
    const updated = await updateVaultFolder(folderId, payload);
    if (isMountedRef.current) {
      setFolders((current) =>
        current.map((folder) => (folder.id === folderId ? updated : folder))
      );
    }
    return updated;
  }

  async function handleDelete(folderId: number) {
    try {
      await deleteVaultFolder(folderId);
      if (!isMountedRef.current) return;
      setFolders((current) => current.filter((folder) => folder.id !== folderId));
      setEditState({ mode: "idle" });
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (loading) {
    return (
      <div className="rounded-[30px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/95 p-10 text-sm text-[var(--text-muted,#94a3b8)] shadow-shell backdrop-blur">
        Loading services...
      </div>
    );
  }

  if (error && folders.length === 0) {
    return (
      <div className="rounded-[30px] border border-red-200 bg-red-50/80 p-6 text-sm text-danger shadow-shell">
        {error}
      </div>
    );
  }

  if (folders.length === 0 && editState.mode !== "create") {
    return (
      <EmptyState onCreate={() => setEditState({ mode: "create" })} />
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
          {folders.length} service{folders.length === 1 ? "" : "s"}
        </p>
        {editState.mode !== "create" ? (
          <button
            type="button"
            onClick={() => setEditState({ mode: "create" })}
            className="inline-flex h-9 items-center rounded-xl bg-[var(--accent,#6366f1)] px-3 text-xs font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)]"
          >
            New service
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      {editState.mode === "create" ? (
        <FolderForm
          mode="create"
          onCancel={() => setEditState({ mode: "idle" })}
          onSubmit={handleCreate}
          onNotice={(msg) => setError(msg)}
        />
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {folders.map((folder) => {
          if (editState.mode === "edit" && editState.folderId === folder.id) {
            return (
              <FolderForm
                key={`edit-${folder.id}`}
                mode="edit"
                initial={{
                  name: folder.name,
                  description: folder.description,
                  outreach_instructions: folder.outreach_instructions,
                  default_sender_account_id: folder.default_sender_account_id
                }}
                onCancel={() => setEditState({ mode: "idle" })}
                onSubmit={(payload) => handleUpdate(folder.id, payload)}
              />
            );
          }
          return (
            <FolderCard
              key={folder.id}
              folder={folder}
              isDeleting={
                editState.mode === "delete" && editState.folderId === folder.id
              }
              onEdit={() =>
                setEditState({ mode: "edit", folderId: folder.id })
              }
              onAskDelete={() =>
                setEditState({ mode: "delete", folderId: folder.id })
              }
              onCancelDelete={() => setEditState({ mode: "idle" })}
              onConfirmDelete={() => handleDelete(folder.id)}
            />
          );
        })}
      </div>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex min-h-[340px] flex-col items-center justify-center rounded-[30px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]/95 p-10 text-center shadow-shell backdrop-blur">
      <h2 className="text-lg font-semibold text-[var(--text,#0f172a)]">No services yet</h2>
      <p className="mt-2 max-w-md text-sm text-[var(--text-dim,#475569)]">
        Add the first service you offer — for example, &ldquo;Custody&rdquo;,
        &ldquo;Stock Loan&rdquo;, or &ldquo;Margin Financing&rdquo;. A short
        description helps the AI write more credible cold-email drafts.
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="mt-6 inline-flex h-10 items-center rounded-xl bg-[var(--accent,#6366f1)] px-4 text-sm font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)]"
      >
        Add your first service
      </button>
    </div>
  );
}

function FolderCard({
  folder,
  isDeleting,
  onEdit,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete
}: {
  folder: VaultFolder;
  isDeleting: boolean;
  onEdit: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  return (
    <article className="flex flex-col rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 shadow-sm">
      <h3 className="text-base font-semibold text-[var(--text,#0f172a)]">{folder.name}</h3>
      <p className="mt-2 flex-1 whitespace-pre-wrap text-sm leading-6 text-[var(--text-dim,#475569)]">
        {folder.description ? folder.description : (
          <span className="italic text-[var(--text-muted,#94a3b8)]">No description.</span>
        )}
      </p>
      <p className="mt-3 text-[11px] uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
        Updated {formatTimestamp(folder.updated_at)}
      </p>
      {isDeleting ? (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2">
          <p className="text-xs text-danger">
            Delete &ldquo;{folder.name}&rdquo;? This cannot be undone.
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancelDelete}
              className="text-xs font-medium text-[var(--text-dim,#475569)] underline-offset-4 hover:underline"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirmDelete}
              className="inline-flex h-8 items-center rounded-lg bg-danger px-3 text-xs font-semibold text-white transition hover:bg-[#c62a2a]"
            >
              Delete
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex justify-end gap-3">
          <Link
            href={`/vault/${folder.id}`}
            className="text-xs font-medium text-[var(--accent,#6366f1)] underline-offset-4 transition hover:underline"
          >
            Manage files & instructions →
          </Link>
          <button
            type="button"
            onClick={onEdit}
            className="text-xs font-medium text-[var(--text-dim,#475569)] underline-offset-4 transition hover:text-[var(--accent,#6366f1)] hover:underline"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onAskDelete}
            className="text-xs font-medium text-[var(--text-muted,#94a3b8)] underline-offset-4 transition hover:text-danger hover:underline"
          >
            Delete
          </button>
        </div>
      )}
    </article>
  );
}

function FolderForm({
  mode,
  initial,
  onCancel,
  onSubmit,
  onNotice
}: {
  mode: "create" | "edit";
  initial?: VaultFolderCreate;
  onCancel: () => void;
  onSubmit: (payload: VaultFolderCreate) => Promise<VaultFolder>;
  // Surfaces a page-level notice that must outlive the form — e.g. "service
  // created but 2 files failed to upload". Shown on the /vault list after the
  // form closes. Create mode only.
  onNotice?: (message: string) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [instructions, setInstructions] = useState(
    initial?.outreach_instructions ?? ""
  );
  const [defaultSenderAccountId, setDefaultSenderAccountId] = useState<
    string | null
  >(initial?.default_sender_account_id ?? null);
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function addStagedFiles(files: File[]) {
    setStagedFiles((current) => {
      // Dedupe by name + size so re-picking the same file doesn't double it.
      const seen = new Set(current.map((f) => `${f.name}:${f.size}`));
      const next = [...current];
      for (const file of files) {
        const key = `${file.name}:${file.size}`;
        if (!seen.has(key) && next.length < MAX_FILES) {
          seen.add(key);
          next.push(file);
        }
      }
      return next;
    });
  }

  function removeStagedFile(index: number) {
    setStagedFiles((current) => current.filter((_, i) => i !== index));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const trimmedName = name.trim();
    if (!trimmedName) {
      setFormError("Name is required.");
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const saved = await onSubmit({
        name: trimmedName,
        description,
        outreach_instructions: instructions,
        default_sender_account_id: defaultSenderAccountId
      });

      // Create mode: now that the folder row exists, upload staged files
      // sequentially (the BE doesn't batch). A file failure doesn't undo
      // the created folder — collect the names and report them after close
      // so the user can retry on the detail page.
      if (mode === "create" && stagedFiles.length > 0) {
        const failed: string[] = [];
        for (let i = 0; i < stagedFiles.length; i += 1) {
          const file = stagedFiles[i];
          setUploadProgress(
            `Uploading file ${i + 1} of ${stagedFiles.length}: ${file.name}`
          );
          try {
            await uploadVaultFile(saved.id, file);
          } catch {
            failed.push(file.name);
          }
        }
        setUploadProgress(null);
        if (failed.length > 0) {
          const noun = failed.length === 1 ? "file" : "files";
          onNotice?.(
            `“${saved.name}” was created, but ${failed.length} ${noun} couldn't be uploaded (${failed.join(", ")}). Open the service to retry.`
          );
        }
      }

      onCancel();
    } catch (err) {
      if (err instanceof ApiError) {
        setFormError(err.detail);
      } else if (err instanceof Error) {
        setFormError(err.message);
      } else {
        setFormError("Could not save the service.");
      }
      setSubmitting(false);
      setUploadProgress(null);
    }
  }

  const busy = submitting || uploadProgress !== null;

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 shadow-sm md:col-span-2 xl:col-span-3"
    >
      <h3 className="text-sm font-semibold text-[var(--text,#0f172a)]">
        {mode === "create" ? "New service" : "Edit service"}
      </h3>
      <div className="mt-3 space-y-4">
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
          Name
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX}
            placeholder="Custody"
            autoFocus
            className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20"
          />
        </label>
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={DESCRIPTION_MAX}
            rows={6}
            placeholder="What you offer, your differentiators, typical client profile, pricing posture..."
            className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20"
          />
          <span className="mt-1 block text-[11px] text-[var(--text-muted,#94a3b8)]">
            {description.length.toLocaleString()} / {DESCRIPTION_MAX.toLocaleString()}{" "}
            characters
          </span>
        </label>

        <VaultInstructionsEditor
          value={instructions}
          onChange={setInstructions}
          maxLength={INSTRUCTIONS_MAX}
          disabled={busy}
        />

        <DefaultSenderSelect
          value={defaultSenderAccountId}
          onChange={setDefaultSenderAccountId}
          disabled={busy}
        />

        {mode === "create" ? (
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
              Reference files
              <span className="ml-2 normal-case tracking-normal text-[var(--text-muted,#94a3b8)]">
                {stagedFiles.length} / {MAX_FILES}
              </span>
            </p>
            <p className="mt-1 text-[11px] leading-4 text-[var(--text-muted,#94a3b8)]">
              Attached files are extracted and embedded so the Outreach AI can
              pull relevant passages into each draft. They upload when you
              create the service.
            </p>
            <div className="mt-2">
              <VaultFileUploader
                disabled={busy || stagedFiles.length >= MAX_FILES}
                onStage={addStagedFiles}
              />
            </div>
            {stagedFiles.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {stagedFiles.map((file, index) => (
                  <li
                    key={`${file.name}:${file.size}:${index}`}
                    className="flex items-center justify-between gap-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]/60 px-3 py-2"
                  >
                    <span className="min-w-0 flex-1 truncate text-xs text-[var(--text,#0f172a)]">
                      {file.name}
                    </span>
                    <span className="shrink-0 text-[11px] text-[var(--text-muted,#94a3b8)]">
                      {formatFileSize(file.size)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeStagedFile(index)}
                      disabled={busy}
                      aria-label={`Remove ${file.name}`}
                      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface,#ffffff)] hover:text-danger disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>

      {uploadProgress ? (
        <div className="mt-3 rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)]/60 px-3 py-2 text-xs text-[var(--text-dim,#475569)]">
          {uploadProgress}
        </div>
      ) : null}

      {formError ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {formError}
        </div>
      ) : null}

      <div className="mt-4 flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="inline-flex h-10 items-center rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 text-sm font-medium text-[var(--text-dim,#475569)] transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={busy}
          className="inline-flex h-10 items-center rounded-xl bg-[var(--accent,#6366f1)] px-4 text-sm font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Saving..." : mode === "create" ? "Create service" : "Save changes"}
        </button>
      </div>
    </form>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatTimestamp(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit"
    });
  } catch {
    return iso;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
