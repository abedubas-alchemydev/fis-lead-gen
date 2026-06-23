"use client";

import type { ReactNode } from "react";

import { useInView } from "@/lib/hooks/use-in-view";

type RevealAnimation = "fade-in" | "fade-in-left" | "fade-in-right" | "scale-in";

// Scroll-reveal wrapper so server sections can opt a child into an entrance
// animation without becoming client components themselves. Wraps `useInView`:
// the element plays one of the existing globals.css keyframes the first time it
// scrolls into view.
//
// Content can never get stuck hidden. The server and the first client render
// emit the VISIBLE resting state (no `opacity-0`) — so SSR, no-JS, crawlers,
// and full-page screenshots all show the content. The hidden state is a pure
// client-side progressive enhancement: `useInView` only `armed`s it after
// mount, and only when motion is allowed, IntersectionObserver exists, and the
// element is below the fold. Reduced motion / no-IO leave it un-armed (stays
// visible), and a safety timeout in the hook reveals anything the observer
// hasn't caught. So the `opacity-0` class applies strictly while
// `armed && !inView`.
export function Reveal({
  children,
  animation = "fade-in",
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  animation?: RevealAnimation;
  delay?: number;
  className?: string;
}) {
  const { ref, inView, armed } = useInView<HTMLDivElement>();

  // Three states, all defaulting to plain-visible:
  //   • armed && !inView  → hidden (post-mount, below-fold, motion allowed)
  //   • armed && inView   → play the entrance animation (scrolled into view)
  //   • !armed            → plain visible, NO animation — covers SSR/first
  //     render, reduced motion, missing IntersectionObserver, the safety
  //     timeout, and elements already on screen at mount. This is what keeps
  //     reduced-motion users animation-free (the fade-in/scale-in keyframes
  //     aren't neutralised by the globals.css reduce guard, so we simply never
  //     apply them here).
  const stateClass = !armed ? "" : inView ? `animate-${animation}` : "opacity-0";

  return (
    <div
      ref={ref}
      className={`${stateClass} ${className}`.trim()}
      style={armed && inView && delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
