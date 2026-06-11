"use client";

import clsx from "clsx";
import { Check, Copy, RotateCcw } from "lucide-react";
import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState, type AnchorHTMLAttributes, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import { draftForMessage } from "./chatbot-draft";
import { ChatbotDraftCard } from "./chatbot-draft-card";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
  pending?: boolean;
  error?: boolean;
  // Active tool-call indicator shown alongside the typing dots while
  // Doxie is executing a tool (e.g. "Searching broker-dealers…"). Cleared
  // when the tool completes or the model starts streaming text.
  toolStatus?: string;
  // Set to true once the assistant turn has fully streamed (a ``done``
  // event landed or the message was loaded from history). The renderer
  // only parses markdown when this is true so a half-streamed
  // ``[Apex](/master-li`` doesn't flash a broken link mid-stream.
  isFinalized?: boolean;
  // FE-only message (e.g. the conversational Vault-upload exchange) that
  // renders in the thread but is NOT sent to the model as history.
  localOnly?: boolean;
  // Error bubbles produced by a failed chat turn can offer a Retry button
  // (re-runs the same turn). Other error bubbles — e.g. a failed
  // new-chat call — have no turn to re-run, so they stay plain.
  retryable?: boolean;
}

// Prefixes the FE recognises as in-app routes. URLs starting with one of
// these render as <Link> for client-side navigation; everything else
// renders as an <a target="_blank"> so external sites open in a new tab
// (and chat state isn't lost).
//
// Keep this in sync with INTERNAL_ROUTE_PREFIXES in
// backend/app/services/chatbot_urls.py — both stacks share the same
// allowlist so Doxie's emitted URLs route consistently.
const INTERNAL_ROUTE_PREFIXES = [
  "/advisor-list",
  "/alerts",
  "/dashboard",
  "/email-extractor",
  "/institutional-investors",
  "/investors",
  "/master-list",
  "/my-favorites",
  "/outreach",
  "/settings",
  "/vault",
  "/visited-firms",
] as const;

function isInternalUrl(href: string): boolean {
  // Internal links always start with ``/`` and a known route prefix.
  // ``//example.com`` (protocol-relative) is intentionally NOT internal
  // — it's an external link that just happens to start with /.
  if (!href.startsWith("/") || href.startsWith("//")) return false;
  return INTERNAL_ROUTE_PREFIXES.some(
    (prefix) => href === prefix || href.startsWith(`${prefix}/`) || href.startsWith(`${prefix}?`),
  );
}

function isUnsafeUrl(href: string): boolean {
  // Defence-in-depth — rehype-sanitize already rejects these schemes,
  // but a custom ``a`` component bypasses some of its filtering, so we
  // re-check here. The lowercased + trimmed comparison guards against
  // sneaky inputs like ``  JaVaScRiPt: alert(1)``.
  const normalised = href.trim().toLowerCase();
  return (
    normalised.startsWith("javascript:") ||
    normalised.startsWith("data:") ||
    normalised.startsWith("vbscript:")
  );
}

function TypingDots() {
  return (
    <span
      role="status"
      aria-label="Doxie is typing"
      className="inline-flex items-center gap-1 py-0.5"
    >
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60" />
    </span>
  );
}

function ChatLink({
  href,
  children,
  onInternalNavigate,
  ...rest
}: AnchorHTMLAttributes<HTMLAnchorElement> & {
  onInternalNavigate?: () => void;
}) {
  // Doxie-emitted URLs are dynamic strings (not statically-known routes),
  // so we use ``useRouter().push`` for client-side navigation instead of
  // ``next/link`` — Link's typed-routes mode rejects ``string`` hrefs.
  const router = useRouter();

  // Bail on unsafe schemes and any anchor with no href — render the
  // visible text as plain text so the bubble still reads naturally.
  if (!href || isUnsafeUrl(href)) {
    return <span>{children}</span>;
  }

  if (isInternalUrl(href)) {
    // Keep the ``<a href>`` so middle-click / Cmd-click / right-click
    // "open in new tab" still work — only intercept ordinary
    // left-clicks for SPA navigation.
    return (
      <a
        href={href}
        onClick={(event) => {
          if (
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.button !== 0
          ) {
            return;
          }
          event.preventDefault();
          // ``href`` is a dynamic string from a Doxie reply; we've
          // already validated it against INTERNAL_ROUTE_PREFIXES, so
          // the ``as Route`` cast is safe (matches the same pattern at
          // app/(app)/email-extractor/page.tsx:123 for dynamic ids).
          router.push(href as Route);
          // Lets the parent collapse the chat panel after the user
          // clicks through — otherwise the destination page is hidden
          // behind the floating widget.
          onInternalNavigate?.();
        }}
        className="font-medium text-[var(--doxie,#6366f1)] underline decoration-[var(--doxie,#6366f1)]/40 underline-offset-2 hover:decoration-[var(--doxie,#6366f1)]"
      >
        {children}
      </a>
    );
  }

  // External: open in a new tab, never leak the chat session via Referer.
  return (
    <a
      {...rest}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-[var(--doxie,#6366f1)] underline decoration-[var(--doxie,#6366f1)]/40 underline-offset-2 hover:decoration-[var(--doxie,#6366f1)]"
    >
      {children}
    </a>
  );
}

function AssistantMarkdown({
  content,
  onInternalNavigate,
}: {
  content: string;
  onInternalNavigate?: () => void;
}) {
  return (
    <div className="space-y-2 [&>p]:m-0 [&>p+p]:mt-2 [&_ul]:my-1 [&_ol]:my-1 [&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-5 [&_ol]:pl-5 [&_code]:rounded [&_code]:bg-black/5 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em]">
      <ReactMarkdown
        // Sanitize the rendered HTML — strips raw HTML, scripts, and
        // any href with a non-allowlisted scheme. Belt-and-braces with
        // the per-link checks in ChatLink above.
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ href, children, ...rest }) => (
            <ChatLink
              href={typeof href === "string" ? href : undefined}
              onInternalNavigate={onInternalNavigate}
              {...rest}
            >
              {children as ReactNode}
            </ChatLink>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function ChatbotMessage({
  role,
  content,
  pending,
  error,
  toolStatus,
  isFinalized,
  onInternalNavigate,
  onRetry,
}: {
  role: ChatRole;
  content: string;
  pending?: boolean;
  error?: boolean;
  toolStatus?: string;
  isFinalized?: boolean;
  onInternalNavigate?: () => void;
  // Present only on retryable error bubbles — renders a Retry button that
  // re-runs the failed turn.
  onRetry?: () => void;
}) {
  const isUser = role === "user";
  const [copied, setCopied] = useState(false);
  // Show the pending-state bubble (dots, optionally with a tool label)
  // only when no text has streamed in yet. Once any token arrives,
  // switch to rendering the accumulating content.
  const showPendingState = pending && content.length === 0;

  // Markdown rendering is reserved for finalised assistant replies. User
  // messages stay plain (they typed them; no need to interpret markdown)
  // and streaming text stays plain to avoid mid-stream "[Apex Clearing
  // ](/master-li" flicker — once the ``done`` event lands, ``isFinalized``
  // flips and the bubble re-renders with parsed markdown + clickable
  // links. Error bubbles stay plain too — the error text is FE-authored.
  const shouldRenderMarkdown = !isUser && !error && isFinalized && content.length > 0;

  // Copy lives only on settled assistant replies — streaming text would
  // copy a half-answer, and FE-authored error text isn't worth copying.
  const showCopy = shouldRenderMarkdown;

  // Interactive outreach-draft card. Only finalized assistant replies
  // qualify, and only when the just-streamed turn captured draft data
  // for this exact content (live session only — reloaded history has no
  // capture, so it renders as plain text like before).
  const draft = shouldRenderMarkdown ? draftForMessage(content) : undefined;

  function handleCopy() {
    if (!navigator.clipboard) return;
    navigator.clipboard
      .writeText(content)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard permission denied — button just stays "Copy" */
      });
  }

  return (
    <div className={clsx("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div className={clsx("flex max-w-[80%] flex-col", isUser ? "items-end" : "items-start")}>
        <div
          className={clsx(
            "whitespace-pre-wrap break-words px-3 py-2 text-sm shadow-sm",
            isUser
              ? "rounded-2xl rounded-tr-md bg-[var(--doxie,#6366f1)] text-white"
              : error
                ? "rounded-2xl rounded-tl-md bg-[var(--surface-2,#f1f6fd)] text-[var(--danger,#dc2626)]"
                : "rounded-2xl rounded-tl-md bg-[var(--surface-2,#f1f6fd)] text-[var(--text,#0f172a)]"
          )}
        >
          {showPendingState ? (
            toolStatus ? (
              <span className="inline-flex items-center gap-2">
                <TypingDots />
                <span className="text-[var(--text-muted,#94a3b8)]">{toolStatus}</span>
              </span>
            ) : (
              <TypingDots />
            )
          ) : shouldRenderMarkdown ? (
            <AssistantMarkdown content={content} onInternalNavigate={onInternalNavigate} />
          ) : (
            content
          )}
        </div>
        {draft ? (
          <ChatbotDraftCard draft={draft} onInternalNavigate={onInternalNavigate} />
        ) : null}
        {showCopy ? (
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy message"}
            title="Copy message"
            className="mt-1 inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] text-[var(--text-muted,#94a3b8)] opacity-60 transition hover:text-[var(--text,#0f172a)] hover:opacity-100 focus:opacity-100 focus:outline-none focus:ring-1 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            {copied ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={2} />}
            {copied ? "Copied" : "Copy"}
          </button>
        ) : null}
        {error && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            aria-label="Retry"
            className="mt-1 inline-flex items-center gap-1 rounded px-1 py-0.5 text-[11px] font-medium text-[var(--doxie,#6366f1)] transition hover:brightness-110 focus:outline-none focus:ring-1 focus:ring-[var(--doxie,#6366f1)]/40"
          >
            <RotateCcw size={12} strokeWidth={2} />
            Retry
          </button>
        ) : null}
      </div>
    </div>
  );
}
