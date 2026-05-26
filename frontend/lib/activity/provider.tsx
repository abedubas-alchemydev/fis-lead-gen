"use client";

import { useEffect, type ReactNode } from "react";

import { RouteObserver } from "./route-observer";
import { enqueueActivity, type ActivityMeta } from "./tracker";

// Document-level delegated listeners for click / submit / blur. One
// provider mounted at the (app) layout level covers the whole
// authenticated surface — landing/auth pages stay un-instrumented.
//
// Privacy: never read the *value* of an input into meta. Only field
// name, length, populated bool. The server's whitelist scrubber drops
// anything that slips through, but the FE shouldn't be sending PII in
// the first place.

const TRACKED_INPUT_TYPES_BLOCKLIST = new Set([
  "hidden",
  "submit",
  "button",
  "image",
  "file",
  "reset",
]);

type TrackableField = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

function isTrackableField(el: Element): el is TrackableField {
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLSelectElement) return true;
  if (el instanceof HTMLInputElement) {
    return !TRACKED_INPUT_TYPES_BLOCKLIST.has(el.type.toLowerCase());
  }
  return false;
}

function fieldMeta(el: TrackableField): ActivityMeta | null {
  const name = el.name || el.id;
  if (!name) return null;
  const value = "value" in el ? (el.value ?? "") : "";
  const isPassword = el instanceof HTMLInputElement && el.type.toLowerCase() === "password";
  const meta: ActivityMeta = {
    field_populated: value.length > 0,
  };
  if (el.name) meta.field_name = el.name.slice(0, 64);
  if (el.id) meta.field_id = el.id.slice(0, 64);
  // Don't leak password length even as metadata.
  if (!isPassword) meta.field_length = value.length;
  const form = "form" in el ? el.form : null;
  if (form?.id) meta.form_id = form.id.slice(0, 64);
  else if (form?.name) meta.form_id = form.name.slice(0, 64);
  return meta;
}

function readNavLabel(el: HTMLElement): string | undefined {
  const dataLabel = el.getAttribute("data-nav-label") ?? el.getAttribute("data-track-label");
  if (dataLabel) return dataLabel.slice(0, 64);
  const aria = el.getAttribute("aria-label");
  if (aria) return aria.slice(0, 64);
  const text = el.textContent?.trim();
  if (text) return text.slice(0, 64);
  return undefined;
}

export function ActivityProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    if (typeof window === "undefined") return;

    // Fields blurred since the last form submit. Used to skip emitting
    // a redundant ``input_used`` on submit for a field that just
    // emitted on blur (browsers fire blur → submit when the user
    // clicks the submit button). Reset after each submit.
    let recentBlurEmits = new WeakSet<TrackableField>();

    const onClick = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a") as HTMLAnchorElement | null;
      if (anchor) {
        const href = anchor.getAttribute("href");
        if (!href) return;
        // Resolve relative hrefs against the current origin.
        let url: URL;
        try {
          url = new URL(href, window.location.href);
        } catch {
          return;
        }
        const sameHost = url.host === window.location.host;
        const opensNewTab =
          anchor.target === "_blank" || event.metaKey || event.ctrlKey || event.button === 1;
        if (sameHost && !opensNewTab) {
          enqueueActivity("nav_click", window.location.pathname, {
            nav_label: readNavLabel(anchor),
            to_path: url.pathname,
          });
        } else {
          enqueueActivity("link_open", window.location.pathname, {
            host: url.host.slice(0, 80),
            nav_label: readNavLabel(anchor),
          });
        }
        return;
      }
      const btn = target.closest("button") as HTMLButtonElement | null;
      if (btn) {
        // Only tracked buttons emit. Untagged button clicks (modal
        // dismiss, dropdown toggle, etc.) would be too noisy.
        const label = btn.getAttribute("data-track-label");
        if (!label) return;
        enqueueActivity("nav_click", window.location.pathname, {
          button_label: label.slice(0, 64),
        });
      }
    };

    const onBlurCapture = (event: FocusEvent) => {
      const target = event.target;
      if (!(target instanceof Element) || !isTrackableField(target)) return;
      const value = "value" in target ? (target.value ?? "") : "";
      if (value.length === 0) return; // empty blur ≠ "used"
      const meta = fieldMeta(target);
      if (!meta) return;
      recentBlurEmits.add(target);
      enqueueActivity("input_used", window.location.pathname, meta);
    };

    const onSubmitCapture = (event: SubmitEvent) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      for (const el of Array.from(form.elements)) {
        if (!isTrackableField(el)) continue;
        if (recentBlurEmits.has(el)) continue; // already counted on blur
        const meta = fieldMeta(el);
        if (!meta) continue;
        enqueueActivity("input_used", window.location.pathname, meta);
      }
      // Reset the dedupe window — a new fill-cycle starts after submit.
      recentBlurEmits = new WeakSet();
    };

    document.addEventListener("click", onClick, { capture: true });
    document.addEventListener("blur", onBlurCapture, { capture: true });
    document.addEventListener("submit", onSubmitCapture, { capture: true });

    return () => {
      document.removeEventListener("click", onClick, { capture: true });
      document.removeEventListener("blur", onBlurCapture, { capture: true });
      document.removeEventListener("submit", onSubmitCapture, { capture: true });
    };
  }, []);

  return (
    <>
      <RouteObserver />
      {children}
    </>
  );
}
