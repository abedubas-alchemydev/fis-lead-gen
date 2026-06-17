"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

// Small "copy this firm's domain to the clipboard" affordance, shown next to
// the firm website on the BD / advisor detail headers. The domain is a single
// public string (the firm's website host), so this is a deliberate, scoped
// copy — distinct from the page-wide copy-block in the security stack, which
// guards against bulk selection of gated data. Marked `data-allow-copy` to
// signal that intent to the input-guard layer.
export function CopyDomainButton({
  domain,
}: {
  domain: string | null | undefined;
}) {
  const [copied, setCopied] = useState(false);

  if (!domain) return null;
  const value = domain; // narrowed to string

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable or denied — no-op; nothing sensitive is lost.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      data-allow-copy
      aria-label={`Copy domain ${value}`}
      title={copied ? "Copied" : `Copy ${value}`}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" strokeWidth={2} />
      ) : (
        <Copy className="h-3.5 w-3.5" strokeWidth={2} />
      )}
      <span>{copied ? "Copied" : "Copy domain"}</span>
    </button>
  );
}
