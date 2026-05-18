"use client";

import { useState } from "react";

import { OutreachModal } from "@/components/master-list/outreach-modal";
import type { ExecutiveContactItem } from "@/lib/types";

interface OutreachButtonProps {
  brokerDealerId: number;
  brokerDealerName: string;
  contact: ExecutiveContactItem;
}

export function OutreachButton({
  brokerDealerId,
  brokerDealerName,
  contact
}: OutreachButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 rounded-full bg-[var(--accent,#6366f1)] px-3 py-1 text-xs font-medium text-white transition hover:bg-[var(--accent-2,#8b5cf6)]"
      >
        Outreach
      </button>
      {open ? (
        <OutreachModal
          brokerDealerId={brokerDealerId}
          brokerDealerName={brokerDealerName}
          contact={contact}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
