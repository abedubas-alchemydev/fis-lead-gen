"use client";

import Link from "next/link";
import type { Route } from "next";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";

import { Button, buttonBase, buttonSizes } from "@/components/ui/button";
import {
  ApiError,
  generateAdhocOutreachDraft,
  generateAdvisorOutreachDraft,
  generateBankOutreachDraft,
  generateInvestorOutreachDraft,
  generateOutreachDraft,
  getLinkedProviders,
  getOutreachSignature,
  listVaultFolders,
  sendAdhocOutreach,
  sendAdvisorOutreach,
  sendBankOutreach,
  sendInvestorOutreach,
  sendOutreachEmail
} from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import type {
  OutreachContact,
  OutreachEntityKind
} from "@/components/master-list/outreach-button";
import type {
  EmailProviderId,
  LinkedProviderItem,
  VaultFolder
} from "@/lib/types";

// Modal that powers the per-contact "Outreach" button on the firm-detail
// panel. Loads the caller's vault folders, lets them pick a service,
// asks the BE (Gemini Flash) to compose a tailored cold-email draft,
// and lets the user transmit the (possibly edited) draft through their
// own Gmail account. Empty state when the caller has no folders links
// them to /vault to create one. The Gmail-send flow surfaces Google
// re-consent inline when the user's account isn't yet authorized for
// the gmail.send scope.

interface OutreachModalProps {
  // Discriminator: picks the (draft, send) endpoint pair invoked when
  // the user clicks Generate / Send. BD callers pass "broker_dealer"
  // (legacy default for the master-list contact rows); the IA
  // advisor-detail surface passes "advisor". Investor isn't wired into
  // any UI surface yet but the case is handled so future callers can
  // pass it without touching this file.
  entityKind: OutreachEntityKind;
  entityId: number;
  entityName: string;
  contact: OutreachContact;
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

// Per-provider send scopes the backend expects in account.scope after
// the user's first Send click. Mirrors
// backend/app/services/email_providers/<provider>.py:SEND_SCOPE (or
// GMAIL_SEND_SCOPE for Google) so the FE incremental-consent flow asks
// for exactly the scope the BE checks for.
const SEND_SCOPE_BY_PROVIDER: Record<EmailProviderId, string> = {
  google: "https://www.googleapis.com/auth/gmail.send",
  microsoft: "Mail.Send",
  yahoo: "mail-w"
};

const PROVIDER_LABEL: Record<EmailProviderId, string> = {
  google: "Gmail",
  microsoft: "Outlook",
  yahoo: "Yahoo Mail"
};

// "Open Sent folder" URLs per provider. Best-effort -- Yahoo's deep
// link is brittle so we just point at the inbox root; users can find
// Sent from there.
const SENT_FOLDER_URL: Record<EmailProviderId, string> = {
  google: "https://mail.google.com/mail/u/0/#sent",
  microsoft: "https://outlook.office.com/mail/sentitems",
  yahoo: "https://mail.yahoo.com/d/folders/2"
};

// Three-tier fallback so the picker has a predictable default for any
// (folder, linkedProviders) combination. Mirrors the BE resolver in
// outreach.py (``_resolve_sender_account``): folder default first,
// then the first send-scoped account, then the first linked account,
// then null when nothing is linked.
function resolveDefaultSenderAccountId(
  folder: VaultFolder | null,
  linkedProviders: LinkedProviderItem[]
): string | null {
  if (linkedProviders.length === 0) return null;
  if (folder?.default_sender_account_id) {
    const folderDefault = linkedProviders.find(
      (p) => p.account_id === folder.default_sender_account_id
    );
    if (folderDefault) return folderDefault.account_id;
  }
  const withScope = linkedProviders.find((p) => p.has_send_scope);
  return (withScope ?? linkedProviders[0]).account_id;
}

// Backend 412 detail codes per provider. The Gmail path keeps its
// legacy codes ("google_account_not_linked" / "gmail_scope_required")
// for back-compat; Microsoft + Yahoo use the provider-prefixed
// variants. ``decodeLinkAction`` parses these to drive linkSocial
// with the right provider + scope when a 412 lands.
const LINK_RECOVERABLE_CODES = new Set<string>([
  "google_account_not_linked",
  "gmail_scope_required",
  "microsoft_account_not_linked",
  "microsoft_scope_required",
  "yahoo_account_not_linked",
  "yahoo_scope_required"
]);

function decodeLinkAction(
  detail: string,
  fallback: EmailProviderId
): { provider: EmailProviderId; needsSendScope: boolean } | null {
  if (detail === "google_account_not_linked") {
    return { provider: "google", needsSendScope: false };
  }
  if (detail === "gmail_scope_required") {
    return { provider: "google", needsSendScope: true };
  }
  if (detail === "microsoft_account_not_linked") {
    return { provider: "microsoft", needsSendScope: false };
  }
  if (detail === "microsoft_scope_required") {
    return { provider: "microsoft", needsSendScope: true };
  }
  if (detail === "yahoo_account_not_linked") {
    return { provider: "yahoo", needsSendScope: false };
  }
  if (detail === "yahoo_scope_required") {
    return { provider: "yahoo", needsSendScope: true };
  }
  // Future-proof: any other LINK_RECOVERABLE_CODES entry defaults to
  // the currently selected provider with send scope.
  if (LINK_RECOVERABLE_CODES.has(detail)) {
    return { provider: fallback, needsSendScope: true };
  }
  return null;
}

export function OutreachModal({
  entityKind,
  entityId,
  entityName,
  contact,
  onClose
}: OutreachModalProps) {
  const [stage, setStage] = useState<Stage>("loading_folders");
  const [folders, setFolders] = useState<VaultFolder[]>([]);
  const [folderId, setFolderId] = useState<number | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [footer, setFooter] = useState("");
  // Set true once the user touches the Footer field so a late signature
  // fetch can't clobber their edit. Mirrors userOverrodeSenderRef.
  const userEditedFooterRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "subject" | "body">("idle");
  // Toggled when the BE returns 412 <provider>_account_not_linked /
  // <provider>_scope_required so the error block renders a
  // "Grant <provider> access" CTA. ``linkActionProvider`` carries the
  // provider the action targets (may differ from the currently selected
  // provider on the rare case where the BE flips it).
  const [linkActionNeeded, setLinkActionNeeded] = useState(false);
  const [linkActionProvider, setLinkActionProvider] =
    useState<EmailProviderId | null>(null);
  // Linked-account state — drives the "Send from:" email-address
  // dropdown and the "Connect <provider>" empty state. Multi-sender
  // outreach (see plans/multi-sender-outreach): one entry per linked
  // OAuth account, not per provider type.
  const [linkedProviders, setLinkedProviders] = useState<LinkedProviderItem[]>(
    []
  );
  // PK of the currently selected ``account`` row. Resolved via the
  // three-tier fallback in resolveDefaultSenderAccountId. Null only
  // before linked-providers has loaded or when zero accounts are
  // linked (empty state).
  const [senderAccountId, setSenderAccountId] = useState<string | null>(null);
  // True when the user has explicitly picked a sender in this modal
  // session -- prevents folder changes from clobbering their choice.
  const userOverrodeSenderRef = useRef(false);
  const session = authClient.useSession();

  // Portal gate. We render the dialog into ``document.body`` (see the
  // ``createPortal`` at the bottom) so its ``position: fixed`` is sized
  // by the viewport, not by the firm-detail container — that wrapper
  // keeps a ``transform`` from its ``animate-fade-in`` (fill-mode both),
  // which would otherwise become the fixed positioning context and
  // strand the modal in the middle of the (very tall) page. ``mounted``
  // defers the portal to the client so SSR/first paint match.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

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

  // Linked-providers fetch — runs in parallel with the folders fetch so
  // the dropdown is ready by the time the user clicks Generate.
  // Failures here are non-fatal: if the call 500s we leave the picker
  // empty and the BE's 412 path catches the no-account case.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getLinkedProviders();
        if (cancelled || !isMountedRef.current) return;
        setLinkedProviders(result.items);
      } catch {
        // Swallow — empty picker, BE's 412 will guide the user.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Signature fetch — prefills the editable Footer field. Runs in
  // parallel with the folders/providers fetches. Non-fatal on failure:
  // the footer just starts empty. The ref guard means a slow response
  // can't overwrite a footer the user has already edited.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getOutreachSignature();
        if (cancelled || !isMountedRef.current) return;
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
    [folders, folderId]
  );

  // Apply the three-tier default any time the folder or linkedProviders
  // change -- unless the user has already overridden the picker in
  // this session. Clears ``senderAccountId`` cleanly when zero accounts
  // are linked (empty state). The ref-guard means folder switches DO
  // bring the folder's default back if the user hasn't picked yet, but
  // folder switches AFTER a manual pick are honoured (the user's pick
  // sticks even if they navigate folders).
  useEffect(() => {
    if (userOverrodeSenderRef.current) return;
    const next = resolveDefaultSenderAccountId(selectedFolder, linkedProviders);
    setSenderAccountId(next);
  }, [selectedFolder, linkedProviders]);

  const selectedAccount = useMemo<LinkedProviderItem | null>(
    () =>
      linkedProviders.find((p) => p.account_id === senderAccountId) ?? null,
    [linkedProviders, senderAccountId]
  );
  const providerId: EmailProviderId = selectedAccount?.provider ?? "google";
  const senderEmail =
    selectedAccount?.email_address ??
    (session.data?.user?.email ?? null);
  // Drafting works without a recipient address (folder + RAG only), but
  // sending obviously can't. When the contact has no email on file we let
  // the user generate + copy a draft and disable Send with an inline note.
  const recipientHasEmail = !!contact.email;

  async function handleGenerate() {
    if (folderId === null) return;
    setStage("generating");
    setError(null);
    try {
      // Branch on entityKind so the right /outreach/{kind}-draft endpoint
      // runs. The firm payloads differ only in their id field names
      // (broker_dealer_id vs advisor_id vs institutional_investor_id),
      // wrapped by the per-kind helpers in api.ts; the adhoc path carries
      // the recipient email instead of any firm/contact FK.
      let draft;
      if (entityKind === "broker_dealer") {
        draft = await generateOutreachDraft({
          broker_dealer_id: entityId,
          contact_id: contact.id,
          folder_id: folderId
        });
      } else if (entityKind === "advisor") {
        draft = await generateAdvisorOutreachDraft({
          advisor_id: entityId,
          advisor_contact_id: contact.id,
          folder_id: folderId
        });
      } else if (entityKind === "investor") {
        draft = await generateInvestorOutreachDraft({
          institutional_investor_id: entityId,
          investor_contact_id: contact.id,
          folder_id: folderId
        });
      } else if (entityKind === "bank") {
        draft = await generateBankOutreachDraft({
          bank_id: entityId,
          bank_contact_id: contact.id,
          folder_id: folderId
        });
      } else {
        draft = await generateAdhocOutreachDraft({
          folder_id: folderId,
          // Null (not "") when unknown so the optional EmailStr passes —
          // the draft endpoint ignores the address anyway.
          recipient_email: contact.email ?? null,
          recipient_name: contact.name
        });
      }
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
    // Append the (possibly edited) footer beneath the body. Merged on the
    // client so the BE send contract is unchanged and the audit row
    // stores exactly what was transmitted.
    const outgoingBody = footer.trim()
      ? `${body.trimEnd()}\n\n${footer.trim()}`
      : body;
    setStage("sending");
    setError(null);
    setLinkActionNeeded(false);
    setLinkActionProvider(null);
    try {
      // Same entityKind branch as handleGenerate -- POSTs to the
      // matching /outreach/{kind}-send endpoint. provider +
      // sender_account_id are entity-agnostic so the body shape only
      // diverges on the id columns.
      if (entityKind === "broker_dealer") {
        await sendOutreachEmail({
          broker_dealer_id: entityId,
          contact_id: contact.id,
          folder_id: folderId ?? 0,
          subject,
          body: outgoingBody,
          // Server derives provider from the resolved account; we still
          // pass ``provider`` for back-compat with older deploys.
          provider: providerId,
          sender_account_id: senderAccountId
        });
      } else if (entityKind === "advisor") {
        await sendAdvisorOutreach({
          advisor_id: entityId,
          advisor_contact_id: contact.id,
          folder_id: folderId ?? 0,
          subject,
          body: outgoingBody,
          provider: providerId,
          sender_account_id: senderAccountId
        });
      } else if (entityKind === "investor") {
        await sendInvestorOutreach({
          institutional_investor_id: entityId,
          investor_contact_id: contact.id,
          folder_id: folderId ?? 0,
          subject,
          body: outgoingBody,
          provider: providerId,
          sender_account_id: senderAccountId
        });
      } else if (entityKind === "bank") {
        await sendBankOutreach({
          bank_id: entityId,
          bank_contact_id: contact.id,
          folder_id: folderId ?? 0,
          subject,
          body: outgoingBody,
          provider: providerId,
          sender_account_id: senderAccountId
        });
      } else {
        // Adhoc: address by recipient email, no firm/contact FK. Folder is
        // optional here (the sent-history row just shows "—" without one).
        await sendAdhocOutreach({
          recipient_email: contact.email ?? "",
          recipient_name: contact.name,
          folder_id: folderId,
          subject,
          body: outgoingBody,
          sender_account_id: senderAccountId
        });
      }
      if (!isMountedRef.current) return;
      setStage("sent");
    } catch (err) {
      if (!isMountedRef.current) return;
      const message = buildSendErrorMessage(err, providerId);
      const action =
        err instanceof ApiError && err.status === 412
          ? decodeLinkAction(err.detail, providerId)
          : null;
      setError(message);
      setLinkActionNeeded(action !== null);
      setLinkActionProvider(action?.provider ?? null);
      // Roll back to draft so the user can edit + retry; the error
      // block above the action row carries the explanation.
      setStage("draft");
    }
  }

  async function handleLinkProvider(target: EmailProviderId) {
    // Triggers the provider's incremental-consent flow for its send
    // scope. Better Auth merges the new scope onto the existing
    // ``account`` row server-side, then redirects back to callbackURL.
    // The page reloads, so any unsaved edits to subject/body are lost
    // — flagged in the inline message above so the user knows to
    // copy-out first if needed. Microsoft uses the same linkSocial
    // helper as Google; Yahoo (genericOAuth) uses linkOAuth2.
    const sendScope = SEND_SCOPE_BY_PROVIDER[target];
    try {
      if (target === "yahoo") {
        // Better Auth 1.3.6 generic-OAuth client plugin: linkOAuth2 if
        // available, else fall back to signIn.oauth2 which Better Auth
        // treats as a link when the user is already signed in.
        const maybeLink = (authClient as unknown as {
          linkOAuth2?: (args: {
            providerId: string;
            scopes?: string[];
            callbackURL?: string;
          }) => Promise<unknown>;
        }).linkOAuth2;
        if (maybeLink) {
          await maybeLink({
            providerId: "yahoo",
            scopes: [sendScope],
            callbackURL: window.location.href
          });
        } else {
          await authClient.signIn.oauth2({
            providerId: "yahoo",
            callbackURL: window.location.href
          });
        }
        return;
      }
      await authClient.linkSocial({
        provider: target,
        scopes: [sendScope],
        callbackURL: window.location.href
      });
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(
        err instanceof Error
          ? err.message
          : `Failed to open ${PROVIDER_LABEL[target]}.`
      );
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

  const modal = (
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
      <div className="relative w-full max-w-[960px] max-h-[90vh] overflow-y-auto rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 shadow-[0_24px_48px_-16px_rgba(15,23,42,0.45)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
              Compose outreach
            </p>
            <h2
              id="outreach-modal-title"
              className="mt-1 text-lg font-semibold tracking-tight text-[var(--text,#0f172a)]"
            >
              {contact.name}
              {contact.title ? (
                <span className="text-sm font-medium text-[var(--text-muted,#94a3b8)]">
                  {" "}- {contact.title}
                </span>
              ) : null}
            </h2>
            <p className="mt-1 text-xs text-[var(--text-muted,#94a3b8)]">
              {entityName ? (
                <>
                  At{" "}
                  <span className="font-medium text-[var(--text-dim,#475569)]">
                    {entityName}
                  </span>
                  {contact.email ? <>{" "}-{" "}</> : null}
                </>
              ) : null}
              {contact.email ? (
                <a
                  href={`mailto:${contact.email}`}
                  className="text-[var(--accent,#6366f1)] underline-offset-4 hover:underline"
                >
                  {contact.email}
                </a>
              ) : null}
            </p>
            {senderEmail && (stage === "draft" || stage === "sending") ? (
              <p className="mt-1 text-[11px] text-[var(--text-muted,#94a3b8)]">
                Sending as <span className="text-[var(--text-dim,#475569)]">{senderEmail}</span>{" "}
                via{" "}
                <span className="text-[var(--text-dim,#475569)]">
                  {PROVIDER_LABEL[providerId]}
                </span>
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={stage === "generating" || stage === "sending"}
            className="rounded-full p-1 text-[var(--text-muted,#94a3b8)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text-dim,#475569)] disabled:cursor-not-allowed"
            aria-label="Close outreach modal"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {stage === "loading_folders" ? (
          <p className="mt-6 text-sm text-[var(--text-muted,#94a3b8)]">Loading your services...</p>
        ) : null}

        {(stage === "ready" || stage === "generating") && folders.length === 0 ? (
          <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-4 text-sm text-[var(--text-dim,#475569)]">
            <p className="font-medium text-[var(--text,#0f172a)]">No services in your Vault yet.</p>
            <p className="mt-1 text-xs leading-5 text-[var(--text-dim,#475569)]">
              Add a service (e.g. &ldquo;Custody&rdquo;, &ldquo;Stock Loan&rdquo;) with a
              short description before generating drafts.
            </p>
            <Link
              href="/vault"
              className="mt-3 inline-flex h-9 items-center rounded-xl bg-[var(--accent,#6366f1)] px-3 text-xs font-semibold text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
            >
              Open the Vault
            </Link>
          </div>
        ) : null}

        {(stage === "ready" || stage === "generating") && folders.length > 0 ? (
          <div className="mt-5 space-y-3">
            <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
              Service to pitch
              <select
                value={folderId ?? ""}
                onChange={(event) => {
                  // Folder switch implies new service -> re-apply the
                  // new folder's default sender. The override flag is
                  // session-scoped per-folder, not global to the modal.
                  userOverrodeSenderRef.current = false;
                  setFolderId(Number(event.target.value));
                }}
                disabled={stage === "generating"}
                className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
              </select>
            </label>
            {selectedFolder?.description ? (
              <p className="rounded-xl bg-[var(--surface-2,#f1f6fd)] px-3 py-2 text-xs leading-5 text-[var(--text-dim,#475569)]">
                {selectedFolder.description}
              </p>
            ) : (
              <p className="text-xs text-[var(--text-muted,#94a3b8)]">
                This service has no description - drafts will be more generic.
              </p>
            )}
            {linkedProviders.length >= 2 ? (
              <label className="block text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
                Send from
                <select
                  value={senderAccountId ?? ""}
                  onChange={(event) => {
                    userOverrodeSenderRef.current = true;
                    setSenderAccountId(event.target.value || null);
                  }}
                  disabled={stage === "generating"}
                  className="mt-2 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {linkedProviders.map((p) => {
                    const label =
                      p.email_address ?? `${PROVIDER_LABEL[p.provider]} account`;
                    const suffix = p.has_send_scope
                      ? ` (${PROVIDER_LABEL[p.provider]})`
                      : ` (${PROVIDER_LABEL[p.provider]} — will request send access)`;
                    return (
                      <option key={p.account_id} value={p.account_id}>
                        {label}
                        {suffix}
                      </option>
                    );
                  })}
                </select>
              </label>
            ) : null}
            {linkedProviders.length === 0 ? (
              <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-3 text-xs leading-5 text-[var(--text-dim,#475569)]">
                <p className="font-medium text-[var(--text,#0f172a)]">
                  No email accounts connected yet.
                </p>
                <p className="mt-1">
                  Connect a provider so Send can transmit through your own
                  mailbox. You can also click Generate now and connect at the
                  Send step.
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {(
                    ["google", "microsoft", "yahoo"] as const satisfies readonly EmailProviderId[]
                  ).map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => void handleLinkProvider(p)}
                      className={clsx(
                        buttonBase,
                        buttonSizes.sm,
                        "rounded-xl border border-amber-300 bg-white text-[11px] text-amber-800 hover:bg-amber-50",
                      )}
                    >
                      Connect {PROVIDER_LABEL[p]}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {stage === "draft" || stage === "sending" ? (
          <div className="mt-5 space-y-4">
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
                  Subject
                </label>
                <button
                  type="button"
                  onClick={() => void handleCopy("subject")}
                  className="text-xs font-medium text-[var(--accent,#6366f1)] underline-offset-4 transition hover:underline"
                >
                  {copyState === "subject" ? "Copied" : "Copy"}
                </button>
              </div>
              <input
                type="text"
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                disabled={stage === "sending"}
                data-allow-copy
                className="mt-1 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
                  Body
                </label>
                <button
                  type="button"
                  onClick={() => void handleCopy("body")}
                  className="text-xs font-medium text-[var(--accent,#6366f1)] underline-offset-4 transition hover:underline"
                >
                  {copyState === "body" ? "Copied" : "Copy"}
                </button>
              </div>
              <textarea
                value={body}
                onChange={(event) => setBody(event.target.value)}
                disabled={stage === "sending"}
                rows={10}
                data-allow-copy
                className="mt-1 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
                  Footer
                </label>
                <span className="text-[11px] text-[var(--text-muted,#94a3b8)]">
                  Appended below your message
                </span>
              </div>
              <textarea
                value={footer}
                onChange={(event) => {
                  userEditedFooterRef.current = true;
                  setFooter(event.target.value);
                }}
                disabled={stage === "sending"}
                rows={4}
                data-allow-copy
                className="mt-1 block w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-sm leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/20 disabled:cursor-not-allowed disabled:opacity-60"
              />
            </div>
          </div>
        ) : null}

        {stage === "sent" ? (
          <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50/70 px-4 py-5 text-sm text-[var(--text-dim,#475569)]">
            <p className="font-semibold text-emerald-700">Email sent.</p>
            <p className="mt-1 text-xs leading-5 text-[var(--text-dim,#475569)]">
              Delivered to{" "}
              <span className="font-medium text-[var(--text-dim,#475569)]">{contact.email}</span>
              {senderEmail ? (
                <>
                  {" "}from{" "}
                  <span className="font-medium text-[var(--text-dim,#475569)]">
                    {senderEmail}
                  </span>
                </>
              ) : null}
              . A copy is in your {PROVIDER_LABEL[providerId]} Sent folder.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <a
                href={SENT_FOLDER_URL[providerId]}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-9 items-center rounded-xl border border-emerald-300 bg-white px-3 text-xs font-semibold text-emerald-700 transition hover:bg-emerald-50"
              >
                Open {PROVIDER_LABEL[providerId]} Sent folder
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
                onClick={() =>
                  void handleLinkProvider(linkActionProvider ?? providerId)
                }
                className={clsx(
                  buttonBase,
                  buttonSizes.sm,
                  "mt-2 rounded-xl bg-danger text-white hover:bg-red-700",
                )}
              >
                Grant {PROVIDER_LABEL[linkActionProvider ?? providerId]} access
              </button>
            ) : null}
          </div>
        ) : null}

        {(stage === "draft" || stage === "sending") && !recipientHasEmail ? (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-[var(--text-dim,#475569)]">
            This contact has no email on file, so sending is disabled. You can
            still generate, edit, and copy the draft.
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-end gap-3">
          {stage === "draft" ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setSubject("");
                setBody("");
                setError(null);
                setLinkActionNeeded(false);
                setStage("ready");
              }}
            >
              Regenerate
            </Button>
          ) : null}
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={stage === "generating" || stage === "sending"}
          >
            {stage === "draft" || stage === "sent" ? "Done" : "Cancel"}
          </Button>
          {stage === "ready" || stage === "generating" ? (
            <Button
              type="button"
              onClick={() => void handleGenerate()}
              disabled={
                stage === "generating" ||
                folders.length === 0 ||
                folderId === null
              }
            >
              {stage === "generating" ? "Generating..." : "Generate draft"}
            </Button>
          ) : null}
          {stage === "draft" || stage === "sending" ? (
            <Button
              type="button"
              onClick={() => void handleSend()}
              disabled={
                stage === "sending" ||
                !subject.trim() ||
                !body.trim() ||
                !recipientHasEmail
              }
              title={
                recipientHasEmail
                  ? undefined
                  : "No email on file for this contact — drafting only."
              }
            >
              {stage === "sending" ? "Sending..." : "Send"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );

  return mounted ? createPortal(modal, document.body) : null;
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

function buildSendErrorMessage(
  error: unknown,
  providerId: EmailProviderId
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
      return `Connect your ${target} account to send outreach from ${PROVIDER_LABEL[providerId]}.`;
    }
    if (
      error.detail === "gmail_scope_required" ||
      error.detail === "microsoft_scope_required" ||
      error.detail === "yahoo_scope_required"
    ) {
      return `We need permission to send email on your behalf via ${PROVIDER_LABEL[providerId]}. Grant access below to continue.`;
    }
  }
  if (error instanceof ApiError && error.status === 400 && error.detail === "recipient_no_email") {
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
  return errorMessage(error);
}
