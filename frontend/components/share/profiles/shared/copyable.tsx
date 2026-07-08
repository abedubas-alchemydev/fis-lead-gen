import type { ReactNode } from "react";

// Atomic copy/select opt-in for the read-only share surface. The app-wide
// input guards (components/security/use-input-guards.ts + security.css)
// block selection and clipboard access everywhere by default; anything
// inside a [data-allow-copy] / [data-allow-select] container is exempt.
// Wrapping a single value in this span re-enables both for that value only,
// so a viewer can copy an identifier, KPI value, or contact name without
// unlocking bulk page-level selection.
export function Copyable({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span data-allow-copy data-allow-select className={className}>
      {children}
    </span>
  );
}
