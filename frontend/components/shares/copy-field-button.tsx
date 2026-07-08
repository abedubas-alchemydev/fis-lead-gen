"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

// Copy-to-clipboard affordance for share URLs and passwords, following the
// CopyDomainButton pattern (master-list/detail/copy-domain-button.tsx). The
// value is deliberately shareable material, so the button is marked
// `data-allow-copy` to signal that intent to the app-wide copy guard in
// components/security/use-input-guards.ts. Flips to a check for 1.5s on
// success.
export function CopyFieldButton({
  value,
  label,
  iconOnly = false,
  className,
}: {
  value: string;
  // What's being copied, for the aria-label/title ("share URL", "password").
  label: string;
  iconOnly?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable or denied — no-op; the value stays visible
      // on screen so nothing is lost.
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      data-allow-copy
      aria-label={copied ? `${label} copied` : `Copy ${label}`}
      title={copied ? "Copied" : `Copy ${label}`}
      className={clsx(
        "inline-flex shrink-0 items-center gap-1 rounded-md border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30",
        iconOnly ? "p-1.5" : "px-2.5 py-1.5",
        className,
      )}
    >
      {copied ? (
        <Check className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
      ) : (
        <Copy className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
      )}
      {iconOnly ? null : <span>{copied ? "Copied" : "Copy"}</span>}
    </button>
  );
}
