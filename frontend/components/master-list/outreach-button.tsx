"use client";

import type { ExecutiveContactItem } from "@/lib/types";

interface OutreachButtonProps {
  contact: Pick<ExecutiveContactItem, "email" | "phone">;
  disabled?: boolean;
}

export function OutreachButton({ disabled = true }: OutreachButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      title="Outreach (coming soon)"
      className="inline-flex items-center gap-1 rounded-full bg-navy px-3 py-1 text-xs font-medium text-white opacity-60 disabled:cursor-not-allowed"
    >
      Outreach
    </button>
  );
}
