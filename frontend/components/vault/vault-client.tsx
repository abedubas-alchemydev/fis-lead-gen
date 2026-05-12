"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  createVaultFolder,
  deleteVaultFolder,
  listVaultFolders,
  updateVaultFolder
} from "@/lib/api";
import type { VaultFolder } from "@/lib/types";

// Vault page body — folder CRUD UI for the per-user services that back the
// /master-list/{id} Outreach modal. Each folder is a service name plus a
// freeform description; the description text is fed verbatim to Gemini
// when the user clicks "Generate draft".
//
// Layout: empty-state card when the user has nothing yet, otherwise a
// grid of cards with inline edit-in-place + delete-confirm.

const NAME_MAX = 255;
const DESCRIPTION_MAX = 20_000;

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

  async function handleCreate(payload: { name: string; description: string }) {
    const created = await createVaultFolder(payload);
    if (!isMountedRef.current) return;
    setFolders((current) => [created, ...current]);
    setEditState({ mode: "idle" });
  }

  async function handleUpdate(
    folderId: number,
    payload: { name: string; description: string }
  ) {
    const updated = await updateVaultFolder(folderId, payload);
    if (!isMountedRef.current) return;
    setFolders((current) =>
      current.map((folder) => (folder.id === folderId ? updated : folder))
    );
    setEditState({ mode: "idle" });
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
      <div className="rounded-[30px] border border-white/80 bg-white/88 p-10 text-sm text-slate-500 shadow-shell backdrop-blur">
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
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500">
          {folders.length} service{folders.length === 1 ? "" : "s"}
        </p>
        {editState.mode !== "create" ? (
          <button
            type="button"
            onClick={() => setEditState({ mode: "create" })}
            className="inline-flex h-9 items-center rounded-xl bg-navy px-3 text-xs font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54]"
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
        />
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {folders.map((folder) => {
          if (editState.mode === "edit" && editState.folderId === folder.id) {
            return (
              <FolderForm
                key={`edit-${folder.id}`}
                mode="edit"
                initial={{ name: folder.name, description: folder.description }}
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
    <div className="flex min-h-[340px] flex-col items-center justify-center rounded-[30px] border border-white/80 bg-white/88 p-10 text-center shadow-shell backdrop-blur">
      <h2 className="text-lg font-semibold text-navy">No services yet</h2>
      <p className="mt-2 max-w-md text-sm text-slate-600">
        Add the first service you offer — for example, &ldquo;Custody&rdquo;,
        &ldquo;Stock Loan&rdquo;, or &ldquo;Margin Financing&rdquo;. A short
        description helps the AI write more credible cold-email drafts.
      </p>
      <button
        type="button"
        onClick={onCreate}
        className="mt-6 inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54]"
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
    <article className="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="text-base font-semibold text-navy">{folder.name}</h3>
      <p className="mt-2 flex-1 whitespace-pre-wrap text-sm leading-6 text-slate-600">
        {folder.description ? folder.description : (
          <span className="italic text-slate-400">No description.</span>
        )}
      </p>
      <p className="mt-3 text-[11px] uppercase tracking-[0.1em] text-slate-400">
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
              className="text-xs font-medium text-slate-600 underline-offset-4 hover:underline"
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
            className="text-xs font-medium text-blue underline-offset-4 transition hover:underline"
          >
            Manage files & instructions →
          </Link>
          <button
            type="button"
            onClick={onAskDelete}
            className="text-xs font-medium text-slate-500 underline-offset-4 transition hover:text-danger hover:underline"
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
  onSubmit
}: {
  mode: "create" | "edit";
  initial?: { name: string; description: string };
  onCancel: () => void;
  onSubmit: (payload: { name: string; description: string }) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

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
      await onSubmit({ name: trimmedName, description });
    } catch (err) {
      if (err instanceof ApiError) {
        setFormError(err.detail);
      } else if (err instanceof Error) {
        setFormError(err.message);
      } else {
        setFormError("Could not save the service.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:col-span-2 xl:col-span-3"
    >
      <h3 className="text-sm font-semibold text-navy">
        {mode === "create" ? "New service" : "Edit service"}
      </h3>
      <div className="mt-3 space-y-3">
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
          Name
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={NAME_MAX}
            placeholder="Custody"
            autoFocus
            className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20"
          />
        </label>
        <label className="block text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={DESCRIPTION_MAX}
            rows={6}
            placeholder="What you offer, your differentiators, typical client profile, pricing posture..."
            className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20"
          />
          <span className="mt-1 block text-[11px] text-slate-400">
            {description.length.toLocaleString()} / {DESCRIPTION_MAX.toLocaleString()}{" "}
            characters
          </span>
        </label>
      </div>

      {formError ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-danger">
          {formError}
        </div>
      ) : null}

      <div className="mt-4 flex items-center justify-end gap-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="inline-flex h-10 items-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Saving..." : mode === "create" ? "Create service" : "Save changes"}
        </button>
      </div>
    </form>
  );
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
