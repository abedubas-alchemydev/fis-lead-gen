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
      // Best-effort clipboard wipe on regaining focus. Clipboard write
      // outside a user gesture is permission-gated in most browsers; on
      // those, navigator.clipboard.writeText() returns a Promise that
      // rejects with NotAllowedError. A synchronous try/catch never sees
      // the rejection — it escapes as "Uncaught (in promise)
      // NotAllowedError" in the console on every focus/blur cycle. Attach
      // a .catch() so the rejection is handled and silently swallowed.
      navigator.clipboard?.writeText("").catch(() => {
        // intentionally ignored
      });
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

      // Disable F12 / devtools shortcuts. We can't actually prevent the
      // browser menu from opening devtools, but stopping the shortcuts
      // raises the friction floor.
      if (key === "F12") {
        e.preventDefault();
        maybeToast("shortcut", "This shortcut is disabled.");
        logEvent("shortcut_blocked", { key: "F12" });
        return;
      }
      if (ctrlOrMeta && e.shiftKey && (key === "I" || key === "i" || key === "J" || key === "j" || key === "C" || key === "c")) {
        e.preventDefault();
        maybeToast("shortcut", "This shortcut is disabled.");
        logEvent("shortcut_blocked", { key: `Ctrl+Shift+${key.toUpperCase()}` });
        return;
      }
      if (ctrlOrMeta && (key === "U" || key === "u")) {
        e.preventDefault();
        maybeToast("shortcut", "View source is disabled.");
        logEvent("shortcut_blocked", { key: "Ctrl+U" });
        return;
      }
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
