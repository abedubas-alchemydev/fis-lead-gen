"use client";

import { useEffect } from "react";

import { enqueueToast } from "@/components/ui/toaster";

import { reportSecurityEvent, type SecurityEventKind } from "./report-event";

// Tags that legitimately need user-initiated copy/selection. Form inputs
// always pass; anything else opts in via data-allow-copy / data-allow-select.
const COPY_ALLOWED_TAGS = new Set(["INPUT", "TEXTAREA"]);

function isCopyAllowed(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (COPY_ALLOWED_TAGS.has(target.tagName)) return true;
  if (target.isContentEditable) return true;
  return Boolean(target.closest("[data-allow-copy], [data-allow-select]"));
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (COPY_ALLOWED_TAGS.has(target.tagName)) return true;
  if (target.isContentEditable) return true;
  return false;
}

// Toast throttle: at most one "blocked" toast per kind per 3s — without
// this the user gets a popup storm when they hold Ctrl+C or repeatedly
// right-click.
const lastToastAt = new Map<string, number>();

function maybeToast(key: string, body: string): void {
  const now = Date.now();
  const last = lastToastAt.get(key) ?? 0;
  if (now - last < 3000) return;
  lastToastAt.set(key, now);
  enqueueToast({ variant: "info", body, durationMs: 2200 });
}

function logEvent(kind: SecurityEventKind, meta?: Record<string, unknown>): void {
  // 1-in-10 client-side sampling keeps the audit_log table from filling up
  // when a user spam-clicks. Right-click in particular needs this.
  if (Math.random() > 0.1) return;
  reportSecurityEvent(kind, meta);
}

export function useInputGuards(): void {
  useEffect(() => {
    function onContextMenu(e: MouseEvent) {
      e.preventDefault();
      maybeToast("contextmenu", "Right-click is disabled on this app.");
      logEvent("right_click");
    }

    function onCopy(e: ClipboardEvent) {
      if (isCopyAllowed(e.target)) return;
      e.preventDefault();
      e.clipboardData?.setData("text/plain", "");
      maybeToast("copy", "Copying is disabled for security.");
      logEvent("copy_blocked");
    }

    function onCut(e: ClipboardEvent) {
      if (isCopyAllowed(e.target)) return;
      e.preventDefault();
      e.clipboardData?.setData("text/plain", "");
      maybeToast("copy", "Copying is disabled for security.");
      logEvent("copy_blocked", { method: "cut" });
    }

    function onDragStart(e: DragEvent) {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      if (target.tagName === "IMG" || target.matches("[data-no-drag]")) {
        e.preventDefault();
        return;
      }
      if (!isCopyAllowed(target)) {
        e.preventDefault();
      }
    }

    function onSelectStart(e: Event) {
      if (isCopyAllowed(e.target)) return;
      e.preventDefault();
    }

    function onBeforePrint() {
      document.documentElement.setAttribute("data-print-locked", "true");
      maybeToast("print", "Printing is disabled for security.");
      logEvent("print_blocked");
    }

    function onAfterPrint() {
      document.documentElement.removeAttribute("data-print-locked");
    }

    function onVisibilityChange() {
      if (document.visibilityState === "hidden") {
        document.documentElement.setAttribute("data-app-blurred", "true");
      } else {
        document.documentElement.removeAttribute("data-app-blurred");
      }
    }

    function onWindowBlur() {
      document.documentElement.setAttribute("data-app-blurred", "true");
    }

    function onWindowFocus() {
      document.documentElement.removeAttribute("data-app-blurred");
      // NOTE: this used to wipe the clipboard via navigator.clipboard
      // .writeText(""). Removed: window focus carries no user activation,
      // so the call hit the clipboard-read=() Permissions-Policy and
      // logged "ClipboardReadWrite permission has been blocked" on every
      // focus — a warning the browser emits regardless of any .catch().
      // It also clobbered whatever the user had copied in another app.
      // The PrintScreen handler below still clears the clipboard (it runs
      // under a keydown user activation, so the sanitized write is allowed
      // without tripping the policy).
    }

    function onKeyDown(e: KeyboardEvent) {
      const key = e.key;
      const ctrlOrMeta = e.ctrlKey || e.metaKey;

      // Always block PrintScreen — and wipe the clipboard immediately, since
      // PrintScreen on Windows puts the captured bitmap into the clipboard.
      // Same Promise-rejection trap as onWindowFocus above: navigator.
      // clipboard.writeText() returns a Promise whose rejection escapes
      // any surrounding synchronous try/catch. Use .catch() to swallow
      // the NotAllowedError that fires when the page lacks a current
      // user gesture or when the clipboard-write Permissions-Policy
      // hasn't been granted yet.
      if (key === "PrintScreen") {
        e.preventDefault();
        navigator.clipboard?.writeText("").catch(() => {
          // intentionally ignored
        });
        maybeToast("printscreen", "Screen capture is disabled for security.");
        logEvent("clipboard_cleared", { trigger: "printscreen" });
        return;
      }

      // NOTE: F12 / Ctrl+Shift+I/J/C / Ctrl+U (devtools + view-source) are
      // intentionally NOT blocked — developer tools are allowed in every
      // environment. The watermark, copy/selection, and print/screenshot
      // guards below remain the active content-protection layer.
      if (ctrlOrMeta && (key === "S" || key === "s")) {
        e.preventDefault();
        maybeToast("shortcut", "Saving the page is disabled.");
        logEvent("shortcut_blocked", { key: "Ctrl+S" });
        return;
      }
      if (ctrlOrMeta && (key === "P" || key === "p")) {
        e.preventDefault();
        maybeToast("print", "Printing is disabled for security.");
        logEvent("print_blocked", { trigger: "Ctrl+P" });
        return;
      }
      // Ctrl+C / Ctrl+X outside typing surfaces — handled by the copy/cut
      // event listeners, but we also pre-empt the keydown here to suppress
      // the default browser feedback when the target is the document body.
      if (ctrlOrMeta && (key === "C" || key === "c" || key === "X" || key === "x")) {
        if (!isTypingTarget(e.target)) {
          // Don't preventDefault here — let the `copy` event fire so the
          // selection-clear and toast happen in the proper handler. Only
          // log the attempt.
          logEvent("copy_blocked", { key: `Ctrl+${key.toUpperCase()}` });
        }
      }
    }

    document.addEventListener("contextmenu", onContextMenu);
    document.addEventListener("copy", onCopy, true);
    document.addEventListener("cut", onCut, true);
    document.addEventListener("dragstart", onDragStart, true);
    document.addEventListener("selectstart", onSelectStart, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("beforeprint", onBeforePrint);
    window.addEventListener("afterprint", onAfterPrint);
    window.addEventListener("blur", onWindowBlur);
    window.addEventListener("focus", onWindowFocus);

    return () => {
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("copy", onCopy, true);
      document.removeEventListener("cut", onCut, true);
      document.removeEventListener("dragstart", onDragStart, true);
      document.removeEventListener("selectstart", onSelectStart, true);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("beforeprint", onBeforePrint);
      window.removeEventListener("afterprint", onAfterPrint);
      window.removeEventListener("blur", onWindowBlur);
      window.removeEventListener("focus", onWindowFocus);
      document.documentElement.removeAttribute("data-print-locked");
      document.documentElement.removeAttribute("data-app-blurred");
    };
  }, []);
}
