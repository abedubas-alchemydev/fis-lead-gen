"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

// Renders a wrap of pill tags (alternate names, types of business) that
// collapses to the first `collapsedCount` items behind a "See More" toggle so
// firms with long lists don't push the rest of the card off-screen. Shared by
// the broker-dealer and investment-advisor detail pages.
export function CollapsiblePillList({
  items,
  collapsedCount = 6,
}: {
  items: string[];
  collapsedCount?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const canCollapse = items.length > collapsedCount;
  const visible = expanded || !canCollapse ? items : items.slice(0, collapsedCount);

  return (
    <>
      <div className="mt-2 flex flex-wrap gap-2">
        {visible.map((item) => (
          <span
            key={item}
            className="rounded-full border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-1 text-[11px] text-[var(--text-dim,#475569)]"
          >
            {item}
          </span>
        ))}
      </div>
      {canCollapse ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-[var(--accent,#6366f1)] transition hover:brightness-110"
        >
          {expanded ? (
            <>
              Show Less
              <ChevronUp className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </>
          ) : (
            <>
              See More ({items.length - collapsedCount})
              <ChevronDown className="h-3 w-3" strokeWidth={2.5} aria-hidden />
            </>
          )}
        </button>
      ) : null}
    </>
  );
}
