"use client";

import { useEffect, useRef, useState } from "react";

type TiltStyle = {
  transform: string;
  transition: string;
};

const RESTING: TiltStyle = {
  transform: "perspective(1200px) rotateX(0deg) rotateY(0deg)",
  transition: "transform 600ms cubic-bezier(0.16,1,0.3,1)",
};

// Mouse-parallax tilt for a card. Returns a ref to attach to the element and
// an inline `style` that rotates the element toward the pointer. The rotation
// is rAF-throttled (one update per frame, regardless of mousemove frequency)
// and eased back to flat on pointer-leave.
//
// No-ops (stays flat, attaches no listeners) under `prefers-reduced-motion:
// reduce` OR a coarse pointer (touch) — tilt is a desktop-only flourish and
// must never fight a touch scroll. `window`/`matchMedia` are read inside the
// effect so SSR and the first client render both emit the resting transform.
//
// Cleanup removes the listeners and cancels any pending frame on unmount.
export function useTilt<T extends HTMLElement = HTMLDivElement>(maxDeg = 6): {
  ref: React.RefObject<T>;
  style: TiltStyle;
} {
  const ref = useRef<T>(null);
  const frameRef = useRef(0);
  const [style, setStyle] = useState<TiltStyle>(RESTING);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const coarse = window.matchMedia("(pointer: coarse)").matches;
    if (reduced || coarse) return;

    const handleMove = (event: MouseEvent) => {
      if (frameRef.current) return;
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = 0;
        const rect = node.getBoundingClientRect();
        const px = (event.clientX - rect.left) / rect.width - 0.5;
        const py = (event.clientY - rect.top) / rect.height - 0.5;
        setStyle({
          transform: `perspective(1200px) rotateX(${(-py * maxDeg).toFixed(2)}deg) rotateY(${(px * maxDeg).toFixed(2)}deg)`,
          transition: "transform 120ms ease-out",
        });
      });
    };

    const handleLeave = () => {
      if (frameRef.current) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = 0;
      }
      setStyle(RESTING);
    };

    node.addEventListener("mousemove", handleMove);
    node.addEventListener("mouseleave", handleLeave);

    return () => {
      node.removeEventListener("mousemove", handleMove);
      node.removeEventListener("mouseleave", handleLeave);
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [maxDeg]);

  return { ref, style };
}
