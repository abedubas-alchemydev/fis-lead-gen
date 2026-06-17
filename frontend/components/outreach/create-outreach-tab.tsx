"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Save,
  Sparkles,
} from "lucide-react";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import {
  ApiError,
  composeSendOutreach,
  createOutreachDraft,
  deleteOutreachDraft,
  generateAdhocOutreachDraft,
  generateAdvisorOutreachDraft,
  generateInvestorOutreachDraft,
  generateOutreachDraft,
  getLinkedProviders,
  getOutreachSignature,
  listVaultFolders,
  updateOutreachDraft,
} from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import { useToast } from "@/components/ui/use-toast";
import {
  RecipientPicker,
  recipientEmail,
  type RecipientValue,
} from "@/components/outreach/recipient-picker";
import type {
  EmailProviderId,
  LinkedProviderItem,
  OutreachComposeRecipient,
  SavedOutreachDraftDetail,
  SavedOutreachDraftSaveRequest,
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

// A saved draft stores recipients in the compose shape (To = {email, name},
// Cc/Bcc = address strings). Loading one back into the composer rehydrates
// them as `adhoc` chips — lossless for sending (compose-send only needs the
// address + optional name) and the firm/contact linkage isn't needed again.
function draftToRecipients(
  recipients: OutreachComposeRecipient[] | undefined,
): RecipientValue[] {
  return (recipients ?? [])
    .filter((r) => r.email)
    .map((r) => ({ kind: "adhoc", email: r.email, name: r.name ?? null }));
}

function draftEmailsToRecipients(
  emails: string[] | undefined,
): RecipientValue[] {
  return (emails ?? [])
    .filter(Boolean)
    .map((email) => ({ kind: "adhoc", email }));
}

export function CreateOutreachTab({
  initialDraft = null,
  onDraftSaved,
}: {
  // When set, the composer opens pre-filled from this saved draft (the
  // Drafts tab "Edit" flow). The parent remounts via `key` when the chosen
  // draft changes, so hydrating once from useState initializers is enough.
  initialDraft?: SavedOutreachDraftDetail | null;
  // Fired after a draft is saved or sent so the workspace can refresh.
  onDraftSaved?: () => void;
} = {}) {
  const toast = useToast();
  // To / Cc / Bcc, like a real email composer. One message is sent to
  // all of them (compose-send); To & Cc are visible to each other, Bcc
  // is hidden. Cc / Bcc rows stay collapsed until revealed (Gmail-style).
  const [to, setTo] = useState<RecipientValue[]>(() =>
    draftToRecipients(initialDraft?.to),
  );
  const [cc, setCc] = useState<RecipientValue[]>(() =>
    draftEmailsToRecipients(initialDraft?.cc),
  );
  const [bcc, setBcc] = useState<RecipientValue[]>(() =>
    draftEmailsToRecipients(initialDraft?.bcc),
  );
  const [showCc, setShowCc] = useState(() => (initialDraft?.cc?.length ?? 0) > 0);
  const [showBcc, setShowBcc] = useState(
    () => (initialDraft?.bcc?.length ?? 0) > 0,
  );
  const [sentCount, setSentCount] = useState(0);
  const [folders, setFolders] = useState<VaultFolder[]>([]);
  const [foldersLoading, setFoldersLoading] = useState(true);
  const [folderId, setFolderId] = useState<number | null>(
    initialDraft?.folder_id ?? null,
  );
  const [linkedProviders, setLinkedProviders] = useState<LinkedProviderItem[]>(
    [],
  );
  const [senderAccountId, setSenderAccountId] = useState<string | null>(
    initialDraft?.sender_account_id ?? null,
  );
  // A draft's saved sender counts as a user override so the (folder,
  // providers) effect doesn't reset it when the composer loads.
  const userOverrodeSenderRef = useRef(initialDraft?.sender_account_id != null);
  const [subject, setSubject] = useState(initialDraft?.subject ?? "");
  const [body, setBody] = useState(initialDraft?.body ?? "");
  // The draft this composer is bound to. Set when editing an existing draft
  // or after the first Save; drives update-vs-create and delete-on-send.
  const [draftId, setDraftId] = useState<number | null>(
    initialDraft?.id ?? null,
  );
  const [savingDraft, setSavingDraft] = useState(false);
  const [footer, setFooter] = useState("");
  // Default footer = the user's saved signature; kept in a ref so Reset
  // can restore it. userEditedFooterRef guards against a late signature
  // fetch clobbering an in-progress edit.
  const defaultSignatureRef = useRef("");
  const userEditedFooterRef = useRef(false);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [linkActionProvider, setLinkActionProvider] =
    useState<EmailProviderId | null>(null);

  // Captured once at mount (the parent remounts via `key` when the edited
  // draft changes), so the folders effect can stay mount-only and still avoid
  // clobbering a draft's hydrated service without taking a reactive dep.
  const isEditingDraftRef = useRef(initialDraft != null);
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
        // Don't clobber a folder hydrated from a draft being edited; only
        // auto-pick the first service on a fresh compose.
        if (!isEditingDraftRef.current) setFolderId(result[0]?.id ?? null);
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

  // Signature fetch — prefills the editable Footer field and seeds the
  // Reset default. Non-fatal on failure: the footer just starts empty.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getOutreachSignature();
        if (cancelled || !isMountedRef.current) return;
        defaultSignatureRef.current = result.signature;
        if (!userEditedFooterRef.current) setFooter(result.signature);
      } catch {
        // Swallow — empty footer is a fine default.
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

  const hasRecipients = to.length > 0;
  const totalRecipients = to.length + cc.length + bcc.length;
  // compose-send is adhoc-style (one email to the To/Cc/Bcc set, no
  // per-firm endpoint), so a Service folder is never required to SEND.
  // AI draft generation still needs one for RAG context — that's the
  // only gate on the folder.
  const canGenerate =
    hasRecipients &&
    folderId !== null &&
    (stage === "idle" || stage === "error");
  const canSend =
    hasRecipients &&
    subject.trim().length > 0 &&
    body.trim().length > 0 &&
    (stage === "idle" || stage === "error");
  // A draft can be saved as soon as there's anything worth keeping — no
  // recipient or full subject/body required, unlike Send.
  const canSaveDraft =
    (hasRecipients ||
      subject.trim().length > 0 ||
      body.trim().length > 0) &&
    stage !== "generating" &&
    stage !== "sending" &&
    !savingDraft;

  async function handleGenerate() {
    // Draft from the first firm-linked contact (for RAG context); fall
    // back to the first one-off recipient. The same draft is sent to
    // everyone — per-recipient personalization is a later enhancement.
    const draftSource =
      to.find((r) => r.kind === "contact") ?? to[0] ?? null;
    if (!draftSource || folderId === null) return;
    setStage("generating");
    setError(null);
    try {
      let draft;
      if (draftSource.kind === "adhoc") {
        draft = await generateAdhocOutreachDraft({
          folder_id: folderId,
          recipient_email: draftSource.email,
          recipient_name: draftSource.name ?? null,
        });
      } else {
        const { result } = draftSource;
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
    if (to.length === 0 || !subject.trim() || !body.trim()) return;
    // Append the (possibly edited) footer beneath the body. Merged on the
    // client so the BE send contract is unchanged and the audit row
    // stores exactly what was transmitted.
    const outgoingBody = footer.trim()
      ? `${body.trimEnd()}\n\n${footer.trim()}`
      : body;
    setStage("sending");
    setError(null);
    setLinkActionProvider(null);
    try {
      // One email to the whole To/Cc/Bcc set. To & Cc carry display names
      // where known; Cc/Bcc are address-only.
      await composeSendOutreach({
        to: to.map((v) => ({
          email: recipientEmail(v),
          name:
            v.kind === "adhoc" ? v.name ?? null : v.result.contact_name || null,
        })),
        cc: cc.map(recipientEmail),
        bcc: bcc.map(recipientEmail),
        subject,
        body: outgoingBody,
        sender_account_id: senderAccountId,
        folder_id: folderId,
      });
      if (!isMountedRef.current) return;
      setSentCount(to.length + cc.length + bcc.length);
      setStage("sent");
      // A sent draft shouldn't linger in Drafts. Best-effort delete — a
      // failure just leaves a stale draft the user can remove manually, so
      // it must never turn a successful send into an error.
      if (draftId != null) {
        try {
          await deleteOutreachDraft(draftId);
        } catch {
          // swallow — the send already succeeded.
        }
        if (!isMountedRef.current) return;
        setDraftId(null);
        onDraftSaved?.();
      }
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

  // The composer state projected into the draft save shape. To carries
  // display names where known; Cc/Bcc are address-only — the same mapping
  // the send path uses, so a saved draft round-trips back identically.
  function buildDraftPayload(): SavedOutreachDraftSaveRequest {
    return {
      subject,
      body,
      to: to.map((v) => ({
        email: recipientEmail(v),
        name:
          v.kind === "adhoc" ? v.name ?? null : v.result.contact_name || null,
      })),
      cc: cc.map(recipientEmail),
      bcc: bcc.map(recipientEmail),
      sender_account_id: senderAccountId,
      folder_id: folderId,
      // Preserve a Doxie-authored draft's provenance across a human edit;
      // anything else is a manual save.
      source: initialDraft?.source === "doxie" ? "doxie" : "manual",
    };
  }

  async function handleSaveDraft() {
    if (savingDraft) return;
    setSavingDraft(true);
    setError(null);
    try {
      const payload = buildDraftPayload();
      const saved =
        draftId != null
          ? await updateOutreachDraft(draftId, payload)
          : await createOutreachDraft(payload);
      if (!isMountedRef.current) return;
      setDraftId(saved.id);
      toast.success("Draft saved.");
      onDraftSaved?.();
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (isMountedRef.current) setSavingDraft(false);
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
    setTo([]);
    setCc([]);
    setBcc([]);
    setShowCc(false);
    setShowBcc(false);
    setSentCount(0);
    setSubject("");
    setBody("");
    setFooter(defaultSignatureRef.current);
    userEditedFooterRef.current = false;
    setError(null);
    setLinkActionProvider(null);
    setStage("idle");
    userOverrodeSenderRef.current = false;
    // Detach from any draft being edited so a post-reset Save creates a new
    // one rather than overwriting the draft the user just cleared.
    setDraftId(null);
    setSavingDraft(false);
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
            {sentCount > 1
              ? `Your message is on its way to ${sentCount} recipients (To, Cc and Bcc). A copy is in your ${PROVIDER_LABEL[providerId]} Sent folder. It'll appear on the Sent history tab in a moment.`
              : `Your message is on its way. A copy is in your ${PROVIDER_LABEL[providerId]} Sent folder. The send will appear on the Sent history tab in a moment.`}
          </p>
          <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
            <Button type="button" onClick={handleReset}>
              Compose another
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={CARD}>
      <div className="space-y-5">
        <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between gap-2">
              <label className={LABEL}>To</label>
              <div className="flex items-center gap-1">
                {!showCc ? (
                  <button
                    type="button"
                    onClick={() => setShowCc(true)}
                    className="rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--accent,#6366f1)]"
                  >
                    Cc
                  </button>
                ) : null}
                {!showBcc ? (
                  <button
                    type="button"
                    onClick={() => setShowBcc(true)}
                    className="rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--accent,#6366f1)]"
                  >
                    Bcc
                  </button>
                ) : null}
              </div>
            </div>
            <div className="mt-2">
              <RecipientPicker
                value={to}
                onChange={setTo}
                disabled={stage === "generating" || stage === "sending"}
                ariaLabel="To recipients"
              />
            </div>
          </div>

          {showCc ? (
            <div>
              <label className={LABEL}>Cc</label>
              <div className="mt-2">
                <RecipientPicker
                  value={cc}
                  onChange={setCc}
                  disabled={stage === "generating" || stage === "sending"}
                  ariaLabel="Cc recipients"
                />
              </div>
            </div>
          ) : null}

          {showBcc ? (
            <div>
              <label className={LABEL}>Bcc</label>
              <div className="mt-2">
                <RecipientPicker
                  value={bcc}
                  onChange={setBcc}
                  disabled={stage === "generating" || stage === "sending"}
                  ariaLabel="Bcc recipients"
                />
              </div>
            </div>
          ) : null}

          {totalRecipients > 1 ? (
            <p className="text-[11px] text-[var(--text-dim,#475569)]">
              One email goes to everyone. To and Cc recipients can see each
              other; Bcc recipients stay hidden.
            </p>
          ) : null}
        </div>

        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <label className={LABEL} htmlFor="create-outreach-folder">
              Service (optional)
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
              <option value="">— No service —</option>
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

        {hasRecipients ? (
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
              className={clsx(
                buttonBase,
                buttonSizes.md,
                "border border-[rgba(99,102,241,0.3)] bg-[rgba(99,102,241,0.08)] text-[#4338ca] hover:bg-[rgba(99,102,241,0.14)]",
              )}
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

        <div>
          <label className={LABEL} htmlFor="create-outreach-footer">
            Footer
          </label>
          <textarea
            id="create-outreach-footer"
            value={footer}
            onChange={(event) => {
              userEditedFooterRef.current = true;
              setFooter(event.target.value);
            }}
            disabled={stage === "sending" || stage === "generating"}
            rows={5}
            maxLength={5000}
            className={`${INPUT} resize-y leading-6`}
          />
          <p className="mt-2 text-[11px] text-[var(--text-dim,#475569)]">
            Appended beneath the body on send. Prefilled from your{" "}
            <Link
              href="/settings/account"
              className="font-semibold text-[var(--accent,#6366f1)] underline-offset-4 hover:underline"
            >
              saved signature
            </Link>
            .
          </p>
        </div>

        {error ? (
          <div className="flex items-start gap-2 rounded-xl border border-red-500/25 bg-red-500/12 px-4 py-3 text-sm text-[var(--pill-red-text,#b91c1c)]">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div className="flex-1">
              <span>{error}</span>
              {linkActionProvider ? (
                <div className="mt-2">
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() =>
                      void handleLinkProvider(linkActionProvider)
                    }
                  >
                    Grant {PROVIDER_LABEL[linkActionProvider]} access
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={handleReset}
            disabled={stage === "generating" || stage === "sending"}
          >
            Reset
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void handleSaveDraft()}
            disabled={!canSaveDraft}
          >
            {savingDraft ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            {draftId != null ? "Update draft" : "Save draft"}
          </Button>
          <Button
            type="button"
            onClick={() => void handleSend()}
            disabled={!canSend}
          >
            {stage === "sending" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            {stage === "sending" ? "Sending…" : "Send"}
          </Button>
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
