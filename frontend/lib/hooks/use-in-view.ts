"use client";

import { useEffect, useRef, useState } from "react";

// Scroll-reveal primitive. Returns a ref to attach to the element plus two
// flags:
//   • `inView`  — flips true the first time the element crosses the viewport
//                 threshold, then stays true (the observer disconnects on the
//                 first hit — reveals are one-shot, never re-hidden on
//                 scroll-out). Also flips true immediately under reduced
//                 motion, when IntersectionObserver is unavailable, or after a
//                 short safety timeout, so animated content can never stay
//                 hidden.
//   • `armed`   — false during SSR and the first client render; flips true in
//                 the mount effect ONLY when we are actually going to gate on
//                 scroll (motion allowed AND IntersectionObserver present AND
//                 the element isn't already in view). Consumers that hide their
//                 resting state (see Reveal) must key the hidden state on
//                 `armed && !inView`, so the server/no-JS/reduced-motion HTML
//                 ships VISIBLE and the hidden state is a pure client-side
//                 progressive enhancement applied after mount.
//
// Hydration-safe: `window`/IntersectionObserver are read only inside the
// effect, so SSR and the first client render both emit `inView = false` and
// `armed = false`. The resting state a consumer renders for that pair MUST be
// visible.
//
// Accessibility: under `prefers-reduced-motion: reduce` — or when
// IntersectionObserver is unavailable — `inView` is set true immediately and
// the element is never armed, so nothing animated ever starts hidden.
//
// Safety: even when the observer is wired, a `REVEAL_TIMEOUT_MS` fallback flips
// `inView` true if the element hasn't scrolled in by then, so a section can
// never be trapped at its hidden resting state (e.g. a full-page screenshot,
// or a fast scroll past the threshold).
const REVEAL_TIMEOUT_MS = 1200;

export function useInView<T extends HTMLElement = HTMLDivElement>(options?: {
  rootMargin?: string;
  threshold?: number;
}): { ref: React.RefObject<T>; inView: boolean; armed: boolean } {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  const [armed, setArmed] = useState(false);

  const rootMargin = options?.rootMargin ?? "0px 0px -10% 0px";
  const threshold = options?.threshold ?? 0.15;

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || typeof IntersectionObserver === "undefined") {
      // No gating: reveal immediately, stay un-armed (resting state visible).
      setInView(true);
      return;
    }

    // If the element is already on screen at mount there's nothing to reveal —
    // skip arming so it never flashes through the hidden state.
    const rect = node.getBoundingClientRect();
    const alreadyVisible =
      rect.top < window.innerHeight && rect.bottom > 0 && rect.height > 0;
    if (alreadyVisible) {
      setInView(true);
      return;
    }

    // Below the fold: arm the hidden state (post-mount enhancement) and watch.
    setArmed(true);

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setInView(true);
            observer.disconnect();
            break;
          }
        }
      },
      { rootMargin, threshold },
    );

    observer.observe(node);

    // Safety fallback: reveal anyway if the observer hasn't fired in time, so
    // nothing can be trapped at opacity:0 (no-scroll screenshots, fast scroll).
    const timer = window.setTimeout(() => {
      setInView(true);
      observer.disconnect();
    }, REVEAL_TIMEOUT_MS);

    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [rootMargin, threshold]);

  return { ref, inView, armed };
}
