import { Fragment, type ReactNode } from "react";
import { Globe } from "lucide-react";

import { Copyable } from "@/components/share/profiles/shared/copyable";

// Compact website display: strip protocol/www/trailing slash and take the
// host. Mirrors cleanWebsiteDisplay in advisor-detail-client.tsx /
// bank-detail-client.tsx.
function cleanWebsiteDisplay(website: string): string {
  return (
    website
      .replace(/^https?:\/\//i, "")
      .replace(/^www\./i, "")
      .replace(/\/+$/, "")
      .split("/")[0]
      ?.toLowerCase() ?? website
  );
}

export interface ProfileMetaItem {
  // Identifier label ("CRD", "FDIC CERT", …). Labelled values render in the
  // mono identifier style; omit the label for plain items like the firm's
  // location, which render in the default meta tone.
  label?: string;
  value: string;
}

// Shared page header for the read-only share profiles: name h1 + pills row,
// optional subtitle line (e.g. an advisor's legal name), external website
// link, and the identifier meta strip. Mirrors the topbar block on the
// authed detail pages minus every interactive control (favorites, Ask
// Doxie, Refresh, copy-domain, Google search, breadcrumbs).
export function ProfileHeader({
  name,
  pills,
  metaItems,
  website,
  subtitle,
}: {
  name: string;
  pills?: ReactNode;
  metaItems: ProfileMetaItem[];
  website?: string | null;
  // Optional line rendered between the pills row and the website link.
  subtitle?: ReactNode;
}) {
  return (
    <div className="mb-6 min-w-0">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-[24px] font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          <Copyable>{name}</Copyable>
        </h1>
        {pills}
      </div>
      {subtitle}
      {website ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <a
            href={website.startsWith("http") ? website : `https://${website}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-[13px] text-[var(--accent,#6366f1)] transition hover:underline"
          >
            <Globe className="h-3.5 w-3.5" strokeWidth={2} />
            {cleanWebsiteDisplay(website)}
          </a>
        </div>
      ) : null}
      {metaItems.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[var(--text-muted,#94a3b8)]">
          {metaItems.map((item, index) => (
            <Fragment key={`${item.label ?? "meta"}-${index}`}>
              {index > 0 ? <span aria-hidden>·</span> : null}
              <span>
                {item.label ? (
                  <>
                    {item.label}{" "}
                  </>
                ) : null}
                <Copyable
                  className={
                    item.label
                      ? "font-mono text-[var(--text-dim,#475569)]"
                      : undefined
                  }
                >
                  {item.value}
                </Copyable>
              </span>
            </Fragment>
          ))}
        </div>
      ) : null}
    </div>
  );
}
