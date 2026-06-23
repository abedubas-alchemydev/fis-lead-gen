"use client";

import { useEffect, useRef, useState } from "react";

// Scroll-reveal primitive. Returns a ref to attach to the element and an
// `inView` flag that flips true the first time the element crosses the
// viewport threshold, then stays true (the observer disconnects on the
// first hit — reveals are one-shot, never re-hidden on scroll-out).
//
// Hydration-safe: `window`/IntersectionObserver are read only inside the
// effect, so SSR and the first client render both emit `inView = false`.
// Content that depends on this must render in a deterministic, visible
// resting state and merely *enhance* once `inView` flips (see Reveal).
//
// Accessibility: under `prefers-reduced-motion: reduce` — or when
// IntersectionObserver is unavailable — `inView` is set true immediately so
// nothing animated ever stays hidden.
export function useInView<T extends HTMLElement = HTMLDivElement>(options?: {
  rootMargin?: string;
  threshold?: number;
}): { ref: React.RefObject<T>; inView: boolean } {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  const rootMargin = options?.rootMargin ?? "0px 0px -10% 0px";
  const threshold = options?.threshold ?? 0.15;

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }

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
    return () => observer.disconnect();
  }, [rootMargin, threshold]);

  return { ref, inView };
}
