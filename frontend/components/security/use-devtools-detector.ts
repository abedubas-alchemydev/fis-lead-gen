"use client";

import { useEffect } from "react";

import { reportSecurityEvent } from "./report-event";

// Best-effort devtools detection. None of these signals are reliable
// individually; combining them catches most casual cases (F12, Cmd+Opt+I,
// context menu → Inspect). A motivated user with browser extensions or a
// detached devtools window WILL slip through — accept that, log the
// attempt server-side, and rely on the watermark for traceability.

const DOCK_WIDTH_THRESHOLD = 160;
const DOCK_HEIGHT_THRESHOLD = 200;
const DEBUGGER_TIMING_THRESHOLD_MS = 100;
const POLL_INTERVAL_MS = 1500;

export function useDevtoolsDetector(): void {
  useEffect(() => {
    let alreadyOpen = false;

    function setOpen(open: boolean, signal: string) {
      if (open === alreadyOpen) return;
      alreadyOpen = open;
      if (open) {
        document.documentElement.setAttribute("data-devtools-open", "true");
        reportSecurityEvent("devtools_open", { signal });
      } else {
        document.documentElement.removeAttribute("data-devtools-open");
      }
    }

    function detect() {
      // Signal 1 — viewport delta. Devtools docked right/bottom shrinks
      // innerWidth/innerHeight vs the outer window size. False-positive
      // sources: browser zoom (Firefox), narrow window with sidebar, the
      // separate-window devtools mode (no signal).
      const widthDelta = window.outerWidth - window.innerWidth;
      const heightDelta = window.outerHeight - window.innerHeight;
      if (widthDelta > DOCK_WIDTH_THRESHOLD || heightDelta > DOCK_HEIGHT_THRESHOLD) {
        setOpen(true, `viewport_delta_w${widthDelta}_h${heightDelta}`);
        return;
      }

      // Signal 2 — debugger statement timing. When devtools is open the
      // browser pauses on `debugger;` even if no breakpoint is set; the
      // wall-clock delta blows past anything a tight JS loop would take.
      // When devtools is closed, the statement is a no-op (<1ms).
      const t0 = performance.now();
      // eslint-disable-next-line no-debugger
      debugger;
      const elapsed = performance.now() - t0;
      if (elapsed > DEBUGGER_TIMING_THRESHOLD_MS) {
        setOpen(true, `debugger_timing_${Math.round(elapsed)}ms`);
        return;
      }

      setOpen(false, "clear");
    }

    // Signal 3 — console getter trick. console.log evaluates the object's
    // toString/valueOf only when devtools renders it; if our getter ever
    // fires we know the console pane is open. Runs on each poll alongside
    // the other detectors.
    const probe = {
      get id() {
        setOpen(true, "console_getter");
        return "";
      },
    };

    function runConsoleProbe() {
      // No-op when devtools closed; triggers the getter when open.
      // eslint-disable-next-line no-console
      console.log("%c", "", probe);
    }

    detect();
    runConsoleProbe();

    const interval = window.setInterval(() => {
      detect();
      runConsoleProbe();
    }, POLL_INTERVAL_MS);

    return () => {
      window.clearInterval(interval);
      document.documentElement.removeAttribute("data-devtools-open");
    };
  }, []);
}
