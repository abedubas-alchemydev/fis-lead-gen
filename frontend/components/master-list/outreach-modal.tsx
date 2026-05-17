"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  generateOutreachDraft,
  listVaultFolders,
  sendOutreachEmail
} from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import type { ExecutiveContactItem, VaultFolder } from "@/lib/types";

// Modal that powers the per-contact "Outreach" button on the firm-detail
// panel. Loads the caller's vault folders, lets them pick a service,
// asks the BE (Gemini Flash) to compose a tailored cold-email draft,
// and lets the user transmit the (possibly edited) draft through their
// own Gmail account. Empty state when the caller has no folders links
// them to /vault to create one. The Gmail-send flow surfaces Google
// re-consent inline when the user's account isn't yet authorized for
// the gmail.send scope.

interface OutreachModalProps {
  brokerDealerId: number;
  brokerDealerName: string;
  contact: ExecutiveContactItem;
  onClose: () => void;
}

type Stage =
  | "loading_folders"
  | "ready"
  | "generating"
  | "draft"
  | "sending"
  | "sent"
  | "error";

const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

// Backend 412 detail codes — kept here as constants so the recovery
// flow doesn't typo a string compare.
const LINK_RECOVERABLE_CODES = new Set([
  "google_account_not_linked",
  "gmail_scope_required"
]);

export function OutreachModal({
  brokerDealerId,
  brokerDealerName,
  contact,
  onClose
}: OutreachModalProps) {
  const [stage, setStage] = useState<Stage>("loading_folders");
  const [folders, setFolders] = useState<VaultFolder[]>([]);
  const [folderId, setFolderId] = useState<number | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "subject" | "body">("idle");
  // Toggled when the BE returns 412 google_account_not_linked /
  // gmail_scope_required so the error block renders a "Grant Gmail
  // access" CTA alongside the message.
  const [linkActionNeeded, setLinkActionNeeded] = useState(false);
  const session = authClient.useSession();
  const senderEmail = session.data?.user?.email ?? null;

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Esc dismiss is allowed any time except during in-flight network
  // work — we don't want to abandon a paid Gemini call or an
  // in-flight Gmail send halfway.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (stage === "generating" || stage === "sending") return;
      onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [stage, onClose]);

  // Initial folder fetch.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await listVaultFolders();
        if (cancelled || !isMountedRef.current) return;
        setFolders(result);
        setFolderId(result[0]?.id ?? null);
        setStage("ready");
      } catch (err) {
        if (cancelled || !isMountedRef.current) return;
        setError(errorMessage(err));
        setStage("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedFolder = useMemo(
    () => folders.find((f) => f.id === folderId) ?? null,
    [folders, folderId]
  );

  async function handleGenerate() {
    if (folderId === null) return;
    setStage("generating");
    setError(null);
    try {
      const draft = await generateOutreachDraft({
        broker_dealer_id: brokerDealerId,
        contact_id: contact.id,
        folder_id: folderId
      });
      if (!isMountedRef.current) return;
      setSubject(draft.subject);
      setBody(draft.body);
      setStage("draft");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(buildGenerateErrorMessage(err));
      setStage("error");
    }
  }

  async function handleSend() {
    if (!subject.trim() || !body.trim()) return;
    setStage("sending");
    setError(null);
    setLinkActionNeeded(false);
    try {
      await sendOutreachEmail({
        broker_dealer_id: brokerDealerId,
        contact_id: contact.id,
        folder_id: folderId ?? 0,
        subject,
        body
      });
      if (!isMountedRef.current) return;
      setStage("sent");
    } catch (err) {
      if (!isMountedRef.current) return;
      const message = buildSendErrorMessage(err);
      const recoverable =
        err instanceof ApiError &&
        err.status === 412 &&
        LINK_RECOVERABLE_CODES.has(err.detail);
      setError(message);
      setLinkActionNeeded(recoverable);
      // Roll back to draft so the user can edit + retry; the error
      // block above the action row carries the explanation.
      setStage("draft");
    }
  }

  async function handleLinkGmail() {
    // Triggers Google's incremental-consent flow for the gmail.send
    // scope. Better Auth merges the new scope onto the existing
    // ``account`` row server-side, then redirects back to callbackURL.
    // The page reloads, so any unsaved edits to subject/body are lost
    // — flagged in the inline message above so the user knows to
    // copy-out first if needed.
    try {
      await authClient.linkSocial({
        provider: "google",
        scopes: [GMAIL_SEND_SCOPE],
        callbackURL: window.location.href
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to open Google.");
    }
  }

  async function handleCopy(which: "subject" | "body") {
    const text = which === "subject" ? subject : body;
    try {
      await navigator.clipboard.writeText(text);
      setCopyState(which);
      window.setTimeout(() => {
        if (isMountedRef.current) setCopyState("idle");
      }, 1600);
    } catch {
      // Clipboard API can fail on insecure origins; fall back silently
      // and keep the textareas selectable so the user can still copy.
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="outreach-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        aria-hidden
        onClick={() => {
          if (stage !== "generating" && stage !== "sending") onClose();
        }}
        className="absolute inset-0 bg-[rgba(15,23,42,0.55)] backdrop-blur-sm"
      />
      <div className="relative w-full max-w-[640px] rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">
              Compose outreach
            </p>
            <h2
              id="outreach-modal-title"
              className="mt-1 text-lg font-semibold tracking-tight text-navy"
            >
              {contact.name}{" "}
              <span className="text-sm font-medium text-slate-500">
                - {contact.title}
              </span>
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              At <span className="font-medium text-slate-700">{brokerDealerName}</span>
              {contact.email ? (
                <>
                  {" "}-{" "}
                  <a
                    href={`mailto:${contact.email}`}
                    className="text-blue underline-offset-4 hover:underline"
                  >
                    {contact.email}
                  </a>
                </>
              ) : null}
            </p>
            {senderEmail && (stage === "draft" || stage === "sending") ? (
              <p className="mt-1 text-[11px] text-slate-400">
                Sending as <span className="text-slate-600">{senderEmail}</span>
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={stage === "generating" || stage === "sending"}
            className="rounded-full p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:cursor-not-allowed"
            aria-label="Close outreach modal"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {stage === "loading_folders" ? (
          <p className="mt-6 text-sm text-slate-500">Loading your services...</p>
        ) : null}

        {(stage === "ready" || stage === "generating") && folders.length === 0 ? (
          <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-4 text-sm text-slate-700">
            <p className="font-medium text-navy">No services in your Vault yet.</p>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              Add a service (e.g. &ldquo;Custody&rdquo;, &ldquo;Stock Loan&rdquo;) with a
              short description before generating drafts.
            </p>
            <Link
              href="/vault"
              className="mt-3 inline-flex h-9 items-center rounded-xl bg-navy px-3 text-xs font-semibold text-white transition hover:bg-[#112b54]"
            >
              Open the Vault
            </Link>
          </div>
        ) : null}

        {(stage === "ready" || stage === "generating") && folders.length > 0 ? (
          <div className="mt-5 space-y-3">
            <label className="block text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
              Service to pitch
              <select
                value={folderId ?? ""}
                onChange={(event) => setFolderId(Number(event.target.value))}
                disabled={stage === "generating"}
                className="mt-2 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
              </select>
            </label>
            {selectedFolder?.description ? (
              <p className="rounded-xl bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
                {selectedFolder.description}
              </p>
            ) : (
              <p className="text-xs text-slate-400">
                This service has no description - drafts will be more generic.
              </p>
            )}
          </div>
        ) : null}

        {stage === "draft" || stage === "sending" ? (
          <div className="mt-5 space-y-4">
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                  Subject
                </label>
                <button
                  type="button"
                  onClick={() => void handleCopy("subject")}
                  className="text-xs font-medium text-blue underline-offset-4 transition hover:underline"
                >
                  {copyState === "subject" ? "Copied" : "Copy"}
                </button>
              </div>
              <input
                type="text"
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                disabled={stage === "sending"}
                className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
                  Body
                </label>
                <button
                  type="button"
                  onClick={() => void handleCopy("body")}
                  className="text-xs font-medium text-blue underline-offset-4 transition hover:underline"
                >
                  {copyState === "body" ? "Copied" : "Copy"}
                </button>
              </div>
              <textarea
                value={body}
                onChange={(event) => setBody(event.target.value)}
                disabled={stage === "sending"}
                rows={10}
                className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-navy outline-none transition focus:border-blue focus:ring-2 focus:ring-blue/20 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
          </div>
        ) : null}

        {stage === "sent" ? (
          <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50/70 px-4 py-5 text-sm text-slate-700">
            <p className="font-semibold text-emerald-700">Email sent.</p>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              Delivered to{" "}
              <span className="font-medium text-slate-700">{contact.email}</span>
              {senderEmail ? (
                <>
                  {" "}from{" "}
                  <span className="font-medium text-slate-700">
                    {senderEmail}
                  </span>
                </>
              ) : null}
              . A copy is in your Gmail Sent folder.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <a
                href="https://mail.google.com/mail/u/0/#sent"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-9 items-center rounded-xl border border-emerald-300 bg-white px-3 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-50"
              >
                Open Gmail Sent folder
              </a>
              <Link
                href={"/outreach/sent" as Route}
                className="inline-flex h-9 items-center rounded-xl border border-emerald-300 bg-white px-3 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-50"
              >
                View all sent outreach
              </Link>
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-3 py-3 text-xs text-danger">
            <p>{error}</p>
            {linkActionNeeded ? (
              <button
                type="button"
                onClick={() => void handleLinkGmail()}
                className="mt-2 inline-flex h-8 items-center rounded-xl bg-danger px-3 text-xs font-semibold text-white transition hover:bg-red-700"
              >
                Grant Gmail access
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-end gap-3">
          {stage === "draft" ? (
            <button
              type="button"
              onClick={() => {
                setSubject("");
                setBody("");
                setError(null);
                setLinkActionNeeded(false);
                setStage("ready");
              }}
              className="inline-flex h-10 items-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
            >
              Regenerate
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            disabled={stage === "generating" || stage === "sending"}
            className="inline-flex h-10 items-center rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {stage === "draft" || stage === "sent" ? "Done" : "Cancel"}
          </button>
          {stage === "ready" || stage === "generating" ? (
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={
                stage === "generating" ||
                folders.length === 0 ||
                folderId === null
              }
              className="inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {stage === "generating" ? "Generating..." : "Generate draft"}
            </button>
          ) : null}
          {stage === "draft" || stage === "sending" ? (
            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={
                stage === "sending" || !subject.trim() || !body.trim()
              }
              className="inline-flex h-10 items-center rounded-xl bg-navy px-4 text-sm font-semibold text-white shadow-lg shadow-navy/15 transition hover:bg-[#112b54] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {stage === "sending" ? "Sending..." : "Send"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

function buildGenerateErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "Outreach drafts are not configured. Contact an administrator.";
  }
  if (error instanceof ApiError && error.status === 502) {
    return "The drafting service is unavailable. Try again in a moment.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "We couldn't find the firm, contact, or service for this draft.";
  }
  return errorMessage(error);
}

function buildSendErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 412) {
    if (error.detail === "google_account_not_linked") {
      return "Connect your Google account to send outreach from your Gmail.";
    }
    if (error.detail === "gmail_scope_required") {
      return "We need permission to send email on your behalf. Grant access below to continue.";
    }
  }
  if (error instanceof ApiError && error.status === 400 && error.detail === "recipient_no_email") {
    return "This contact has no email on file.";
  }
  if (error instanceof ApiError && error.status === 502) {
    return "Gmail rejected the message. Try again in a moment.";
  }
  if (error instanceof ApiError && error.status === 503) {
    return "Outreach sending is not configured. Contact an administrator.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return "We couldn't find the firm, contact, or service for this send.";
  }
  return errorMessage(error);
}
