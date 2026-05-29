"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Sparkles,
} from "lucide-react";

import {
  ApiError,
  generateAdhocOutreachDraft,
  generateAdvisorOutreachDraft,
  generateInvestorOutreachDraft,
  generateOutreachDraft,
  getLinkedProviders,
  listVaultFolders,
  sendAdhocOutreach,
  sendAdvisorOutreach,
  sendInvestorOutreach,
  sendOutreachEmail,
} from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import {
  RecipientPicker,
  type RecipientValue,
} from "@/components/outreach/recipient-picker";
import type {
  EmailProviderId,
  LinkedProviderItem,
  VaultFolder,
} from "@/lib/types";

const CARD =
  "rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]";
const LABEL =
  "block text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]";
const INPUT =
  "mt-2 block w-full rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] disabled:cursor-not-allowed disabled:opacity-60";

const SEND_SCOPE_BY_PROVIDER: Record<EmailProviderId, string> = {
  google: "https://www.googleapis.com/auth/gmail.send",
  microsoft: "Mail.Send",
  yahoo: "mail-w",
};

const PROVIDER_LABEL: Record<EmailProviderId, string> = {
  google: "Gmail",
  microsoft: "Outlook",
  yahoo: "Yahoo Mail",
};

// Mirrors the outreach-modal stages but compresses them: subject + body
// inputs are always visible (so users can compose without Generate, for
// adhoc sends with no firm context where Gemini can't draft from RAG).
type Stage = "idle" | "generating" | "sending" | "sent" | "error";

const LINK_RECOVERABLE_CODES = new Set<string>([
  "google_account_not_linked",
  "gmail_scope_required",
  "microsoft_account_not_linked",
  "microsoft_scope_required",
  "yahoo_account_not_linked",
  "yahoo_scope_required",
]);

function decodeLinkAction(
  detail: string,
  fallback: EmailProviderId,
): { provider: EmailProviderId; needsSendScope: boolean } | null {
  if (detail === "google_account_not_linked")
    return { provider: "google", needsSendScope: false };
  if (detail === "gmail_scope_required")
    return { provider: "google", needsSendScope: true };
  if (detail === "microsoft_account_not_linked")
    return { provider: "microsoft", needsSendScope: false };
  if (detail === "microsoft_scope_required")
    return { provider: "microsoft", needsSendScope: true };
  if (detail === "yahoo_account_not_linked")
    return { provider: "yahoo", needsSendScope: false };
  if (detail === "yahoo_scope_required")
    return { provider: "yahoo", needsSendScope: true };
  if (LINK_RECOVERABLE_CODES.has(detail))
    return { provider: fallback, needsSendScope: true };
  return null;
}

function resolveDefaultSenderAccountId(
  folder: VaultFolder | null,
  linkedProviders: LinkedProviderItem[],
): string | null {
  if (linkedProviders.length === 0) return null;
  if (folder?.default_sender_account_id) {
    const folderDefault = linkedProviders.find(
      (p) => p.account_id === folder.default_sender_account_id,
    );
    if (folderDefault) return folderDefault.account_id;
  }
  const withScope = linkedProviders.find((p) => p.has_send_scope);
  return (withScope ?? linkedProviders[0]).account_id;
}

export function CreateOutreachTab() {
  const [recipient, setRecipient] = useState<RecipientValue | null>(null);
  const [folders, setFolders] = useState<VaultFolder[]>([]);
  const [foldersLoading, setFoldersLoading] = useState(true);
  const [folderId, setFolderId] = useState<number | null>(null);
  const [linkedProviders, setLinkedProviders] = useState<LinkedProviderItem[]>(
    [],
  );
  const [senderAccountId, setSenderAccountId] = useState<string | null>(null);
  const userOverrodeSenderRef = useRef(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [linkActionProvider, setLinkActionProvider] =
    useState<EmailProviderId | null>(null);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Initial folders + linked-providers fetches in parallel. Folder load
  // failures are non-fatal: the form still works for adhoc sends.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await listVaultFolders();
        if (cancelled || !isMountedRef.current) return;
        setFolders(result);
        setFolderId(result[0]?.id ?? null);
      } catch {
        // Empty list -> the Service select still renders disabled and
        // adhoc sends still work. Show no error toast for this; it'd
        // be confusing on the idle screen.
      } finally {
        if (!cancelled && isMountedRef.current) setFoldersLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getLinkedProviders();
        if (cancelled || !isMountedRef.current) return;
        setLinkedProviders(result.items);
      } catch {
        // Swallow -- empty picker, the send 412 path will guide the user.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedFolder = useMemo(
    () => folders.find((f) => f.id === folderId) ?? null,
    [folders, folderId],
  );

  // Sender default tracks (folder, providers) until the user picks
  // explicitly; mirrors the outreach-modal ref-guard pattern.
  useEffect(() => {
    if (userOverrodeSenderRef.current) return;
    setSenderAccountId(
      resolveDefaultSenderAccountId(selectedFolder, linkedProviders),
    );
  }, [selectedFolder, linkedProviders]);

  const selectedAccount = useMemo<LinkedProviderItem | null>(
    () =>
      linkedProviders.find((p) => p.account_id === senderAccountId) ?? null,
    [linkedProviders, senderAccountId],
  );
  const providerId: EmailProviderId = selectedAccount?.provider ?? "google";

  const isContact = recipient?.kind === "contact";
  const isAdhoc = recipient?.kind === "adhoc";
  // BD/Advisor/Investor sends require folder_id (server validates gt=0).
  // Adhoc sends accept null folder_id.
  const folderRequired = isContact;
  // AI draft generation needs a Service folder for the RAG context,
  // regardless of whether the recipient is a known contact or adhoc.
  // The send path stays unchanged — adhoc sends still don't require a
  // folder; the gate is on Generate only.
  const canGenerate =
    (isContact || isAdhoc) &&
    folderId !== null &&
    (stage === "idle" || stage === "error");
  const canSend =
    !!recipient &&
    subject.trim().length > 0 &&
    body.trim().length > 0 &&
    (folderRequired ? folderId !== null : true) &&
    (stage === "idle" || stage === "error");

  async function handleGenerate() {
    if (!recipient || folderId === null) return;
    setStage("generating");
    setError(null);
    try {
      let draft;
      if (recipient.kind === "adhoc") {
        draft = await generateAdhocOutreachDraft({
          folder_id: folderId,
          recipient_email: recipient.email,
          recipient_name: recipient.name ?? null,
        });
      } else {
        const { result } = recipient;
        if (result.entity_kind === "broker_dealer") {
          draft = await generateOutreachDraft({
            broker_dealer_id: result.entity_id,
            contact_id: result.contact_id,
            folder_id: folderId,
          });
        } else if (result.entity_kind === "advisor") {
          draft = await generateAdvisorOutreachDraft({
            advisor_id: result.entity_id,
            advisor_contact_id: result.contact_id,
            folder_id: folderId,
          });
        } else {
          draft = await generateInvestorOutreachDraft({
            institutional_investor_id: result.entity_id,
            investor_contact_id: result.contact_id,
            folder_id: folderId,
          });
        }
      }
      if (!isMountedRef.current) return;
      setSubject(draft.subject);
      setBody(draft.body);
      setStage("idle");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(buildGenerateErrorMessage(err));
      setStage("error");
    }
  }

  async function handleSend() {
    if (!recipient || !subject.trim() || !body.trim()) return;
    if (folderRequired && folderId === null) return;
    setStage("sending");
    setError(null);
    setLinkActionProvider(null);
    try {
      if (recipient.kind === "adhoc") {
        await sendAdhocOutreach({
          recipient_email: recipient.email,
          recipient_name: recipient.name ?? null,
          subject,
          body,
          sender_account_id: senderAccountId,
          folder_id: folderId,
        });
      } else {
        const { result } = recipient;
        const folderIdSafe = folderId ?? 0;
        if (result.entity_kind === "broker_dealer") {
          await sendOutreachEmail({
            broker_dealer_id: result.entity_id,
            contact_id: result.contact_id,
            folder_id: folderIdSafe,
            subject,
            body,
            provider: providerId,
            sender_account_id: senderAccountId,
          });
        } else if (result.entity_kind === "advisor") {
          await sendAdvisorOutreach({
            advisor_id: result.entity_id,
            advisor_contact_id: result.contact_id,
            folder_id: folderIdSafe,
            subject,
            body,
            provider: providerId,
            sender_account_id: senderAccountId,
          });
        } else {
          await sendInvestorOutreach({
            institutional_investor_id: result.entity_id,
            investor_contact_id: result.contact_id,
            folder_id: folderIdSafe,
            subject,
            body,
            provider: providerId,
            sender_account_id: senderAccountId,
          });
        }
      }
      if (!isMountedRef.current) return;
      setStage("sent");
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(buildSendErrorMessage(err, providerId));
      const action =
        err instanceof ApiError && err.status === 412
          ? decodeLinkAction(err.detail, providerId)
          : null;
      setLinkActionProvider(action?.provider ?? null);
      setStage("error");
    }
  }

  async function handleLinkProvider(target: EmailProviderId) {
    const sendScope = SEND_SCOPE_BY_PROVIDER[target];
    try {
      if (target === "yahoo") {
        const maybeLink = (
          authClient as unknown as {
            linkOAuth2?: (args: {
              providerId: string;
              scopes?: string[];
              callbackURL?: string;
            }) => Promise<unknown>;
          }
        ).linkOAuth2;
        if (maybeLink) {
          await maybeLink({
            providerId: "yahoo",
            scopes: [sendScope],
            callbackURL: window.location.href,
          });
        } else {
          await authClient.signIn.oauth2({
            providerId: "yahoo",
            callbackURL: window.location.href,
          });
        }
        return;
      }
      await authClient.linkSocial({
        provider: target,
        scopes: [sendScope],
        callbackURL: window.location.href,
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(
        err instanceof Error
          ? err.message
          : `Failed to open ${PROVIDER_LABEL[target]}.`,
      );
    }
  }

  function handleReset() {
    setRecipient(null);
    setSubject("");
    setBody("");
    setError(null);
    setLinkActionProvider(null);
    setStage("idle");
    userOverrodeSenderRef.current = false;
  }

  if (stage === "sent") {
    return (
      <div className={CARD}>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <div className="grid h-12 w-12 place-items-center rounded-full bg-emerald-500/12 text-emerald-600">
            <CheckCircle2 className="h-6 w-6" strokeWidth={2} />
          </div>
          <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Email sent
          </h2>
          <p className="max-w-md text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            Your message is on its way. A copy is in your{" "}
            {PROVIDER_LABEL[providerId]} Sent folder. The send will appear on
            the Sent history tab in a moment.
          </p>
          <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
            <button
              type="button"
              onClick={handleReset}
              className="inline-flex h-10 items-center rounded-xl bg-[var(--accent,#6366f1)] px-4 text-[13px] font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)]"
            >
              Compose another
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={CARD}>
      <div className="space-y-5">
        <div>
          <label className={LABEL}>To</label>
          <div className="mt-2">
            <RecipientPicker
              value={recipient}
              onChange={setRecipient}
              disabled={stage === "generating" || stage === "sending"}
              ariaLabel="Recipient"
            />
          </div>
          {isAdhoc ? (
            <p className="mt-2 text-[11px] text-[var(--text-dim,#475569)]">
              Ad-hoc sends generate a draft from the selected service only
              (no firm context). Edit before sending.
            </p>
          ) : null}
        </div>

        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="create-outreach-folder">
              Service {folderRequired ? "" : "(optional)"}
            </label>
            <select
              id="create-outreach-folder"
              value={folderId ?? ""}
              onChange={(event) => {
                userOverrodeSenderRef.current = false;
                const v = event.target.value;
                setFolderId(v ? Number(v) : null);
              }}
              disabled={
                foldersLoading ||
                folders.length === 0 ||
                stage === "generating" ||
                stage === "sending"
              }
              className={INPUT}
            >
              {!folderRequired ? (
                <option value="">— No service —</option>
              ) : null}
              {folders.map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {folder.name}
                </option>
              ))}
            </select>
            {folders.length === 0 && !foldersLoading ? (
              <p className="mt-2 text-[11px] text-[var(--text-dim,#475569)]">
                No services in your{" "}
                <Link
                  href="/vault"
                  className="font-semibold text-[var(--accent,#6366f1)] underline-offset-4 hover:underline"
                >
                  Vault
                </Link>{" "}
                yet. AI drafting needs at least one.
              </p>
            ) : null}
          </div>

          {linkedProviders.length >= 2 ? (
            <div>
              <label className={LABEL} htmlFor="create-outreach-sender">
                Send from
              </label>
              <select
                id="create-outreach-sender"
                value={senderAccountId ?? ""}
                onChange={(event) => {
                  userOverrodeSenderRef.current = true;
                  setSenderAccountId(event.target.value || null);
                }}
                disabled={stage === "generating" || stage === "sending"}
                className={INPUT}
              >
                {linkedProviders.map((p) => {
                  const label =
                    p.email_address ?? `${PROVIDER_LABEL[p.provider]} account`;
                  const suffix = p.has_send_scope
                    ? ` (${PROVIDER_LABEL[p.provider]})`
                    : ` (${PROVIDER_LABEL[p.provider]} — needs send access)`;
                  return (
                    <option key={p.account_id} value={p.account_id}>
                      {label}
                      {suffix}
                    </option>
                  );
                })}
              </select>
            </div>
          ) : null}
        </div>

        {isContact || isAdhoc ? (
          <div>
            <button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={!canGenerate}
              title={
                folderId === null
                  ? "Pick a service above to enable AI draft generation."
                  : undefined
              }
              className="inline-flex h-10 items-center gap-2 rounded-xl border border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.08)] px-3 text-[13px] font-semibold text-[#4338ca] transition hover:bg-[rgba(99,102,241,0.14)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {stage === "generating" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              {stage === "generating"
                ? "Generating…"
                : "Generate draft with AI"}
            </button>
            {folderId === null && folders.length > 0 ? (
              <p className="mt-2 text-[11px] text-[var(--text-dim,#475569)]">
                Select a service above to enable AI draft generation.
              </p>
            ) : null}
          </div>
        ) : null}

        <div>
          <label className={LABEL} htmlFor="create-outreach-subject">
            Subject
          </label>
          <input
            id="create-outreach-subject"
            type="text"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            disabled={stage === "sending" || stage === "generating"}
            maxLength={998}
            className={INPUT}
          />
        </div>

        <div>
          <label className={LABEL} htmlFor="create-outreach-body">
            Body
          </label>
          <textarea
            id="create-outreach-body"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            disabled={stage === "sending" || stage === "generating"}
            rows={12}
            maxLength={100_000}
            className={`${INPUT} resize-y leading-6`}
          />
        </div>

        {error ? (
          <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div className="flex-1">
              <span>{error}</span>
              {linkActionProvider ? (
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={() =>
                      void handleLinkProvider(linkActionProvider)
                    }
                    className="inline-flex h-8 items-center rounded-xl bg-[var(--pill-red-text,#b91c1c)] px-3 text-[12px] font-semibold text-white transition hover:opacity-90"
                  >
                    Grant {PROVIDER_LABEL[linkActionProvider]} access
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={handleReset}
            disabled={stage === "generating" || stage === "sending"}
            className="inline-flex h-10 items-center rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 text-[13px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={() => void handleSend()}
            disabled={!canSend}
            className="inline-flex h-10 items-center gap-2 rounded-xl bg-[var(--accent,#6366f1)] px-4 text-[13px] font-semibold text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 transition hover:bg-[var(--accent-2,#8b5cf6)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {stage === "sending" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {stage === "sending" ? "Sending…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message;
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

function buildSendErrorMessage(
  error: unknown,
  providerId: EmailProviderId,
): string {
  if (error instanceof ApiError && error.status === 412) {
    if (
      error.detail === "google_account_not_linked" ||
      error.detail === "microsoft_account_not_linked" ||
      error.detail === "yahoo_account_not_linked"
    ) {
      const target = error.detail.startsWith("google")
        ? "Google"
        : error.detail.startsWith("microsoft")
          ? "Microsoft"
          : "Yahoo";
      return `Connect your ${target} account to send outreach.`;
    }
    if (
      error.detail === "gmail_scope_required" ||
      error.detail === "microsoft_scope_required" ||
      error.detail === "yahoo_scope_required"
    ) {
      return `We need permission to send email on your behalf via ${PROVIDER_LABEL[providerId]}. Grant access below to continue.`;
    }
  }
  if (
    error instanceof ApiError &&
    error.status === 400 &&
    error.detail === "recipient_no_email"
  ) {
    return "This contact has no email on file.";
  }
  if (error instanceof ApiError && error.status === 502) {
    return `${PROVIDER_LABEL[providerId]} rejected the message. Try again in a moment.`;
  }
  if (error instanceof ApiError && error.status === 503) {
    return `${PROVIDER_LABEL[providerId]} sending is not configured. Contact an administrator.`;
  }
  if (error instanceof ApiError && error.status === 404) {
    return "We couldn't find the firm, contact, or service for this send.";
  }
  if (error instanceof ApiError && error.status === 422) {
    return "Please check the recipient email and try again.";
  }
  return errorMessage(error);
}
