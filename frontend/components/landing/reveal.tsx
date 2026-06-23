"use client";

import type { ReactNode } from "react";

import { useInView } from "@/lib/hooks/use-in-view";

type RevealAnimation = "fade-in" | "fade-in-left" | "fade-in-right" | "scale-in";

// Scroll-reveal wrapper so server sections can opt a child into an entrance
// animation without becoming client components themselves. Wraps `useInView`:
// before the element enters the viewport it sits invisible + nudged; once it
// crosses the threshold it plays one of the existing globals.css keyframes.
//
// Deterministic SSR: the server and the first client render emit the
// pre-reveal state. `useInView` flips immediately under reduced motion (or if
// IntersectionObserver is missing), so the content is shown right away and
// never stays hidden — the opacity-0 resting state only persists once we know
// motion is allowed AND the element hasn't scrolled in yet.
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
  const { ref, inView } = useInView<HTMLDivElement>();

  return (
    <div
      ref={ref}
      className={`${inView ? `animate-${animation}` : "opacity-0"} ${className}`.trim()}
      style={inView && delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
