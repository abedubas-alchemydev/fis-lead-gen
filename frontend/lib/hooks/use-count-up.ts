"use client";

import { useEffect, useState } from "react";

// Cubic ease-out — fast start, gentle settle. Extracted verbatim from the
// original inline implementation in live-dashboard-mockup.tsx.
export function easeOutCubic(t: number) {
  return 1 - Math.pow(1 - t, 3);
}

// Animated number that ramps from 0 → `target` over `durationMs` after an
// optional `delayMs` lead-in, returning a locale-formatted string. Behavior
// is unchanged from the mockup's original hook with one addition: a `start`
// gate so a caller can hold the count at its resting value until the element
// scrolls into view, then trigger the ramp.
//
// Accessibility: under `prefers-reduced-motion: reduce` the value is shown at
// its final `target` immediately — no ramp. Both the timeout and the rAF are
// cleared on unmount / dependency change so nothing leaks.
export function useCountUp(
  target: number,
  durationMs: number,
  delayMs: number,
  start: boolean = true,
): string {
  const [display, setDisplay] = useState(target);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!start) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplay(target);
      return;
    }

    let frame = 0;
    let startTs = 0;
    setDisplay(0);

    const tick = (ts: number) => {
      if (!startTs) startTs = ts;
      const elapsed = ts - startTs;
      const t = Math.min(1, elapsed / durationMs);
      setDisplay(Math.round(target * easeOutCubic(t)));
      if (t < 1) frame = requestAnimationFrame(tick);
    };

    const timer = window.setTimeout(() => {
      frame = requestAnimationFrame(tick);
    }, delayMs);

    return () => {
      window.clearTimeout(timer);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [target, durationMs, delayMs, start]);

  return display.toLocaleString("en-US");
}
