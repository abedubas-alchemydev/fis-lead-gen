import { Globe, Search } from "lucide-react";

// Renders a clickable row directly under the firm-name h1 on
// /master-list/{id}. Pure presentational — no client state, no fetches.
// When `website` is non-empty, surfaces the cleaned hostname as a link
// to the firm's site. When null/empty, falls back to a "Search Google
// for this firm" anchor so users can still recover the workflow for
// firms without a clean domain on file.
export function FirmWebsiteLink({
  firmName,
  website,
}: {
  firmName: string;
  website: string | null;
}) {
  const trimmed = (website ?? "").trim();

  if (trimmed) {
    const href = trimmed.startsWith("http") ? trimmed : `https://${trimmed}`;
    const display =
      trimmed
        .replace(/^https?:\/\//i, "")
        .replace(/^www\./i, "")
        .replace(/\/+$/, "")
        .split("/")[0]
        ?.toLowerCase() ?? trimmed;

    return (
      <div className="mt-1.5">
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-[13px] text-[var(--accent,#6366f1)] transition hover:underline"
        >
          <Globe className="h-3.5 w-3.5" strokeWidth={2} />
          {display}
        </a>
      </div>
    );
  }

  const googleHref = `https://www.google.com/search?q=${encodeURIComponent(firmName)}`;
  return (
    <div className="mt-1.5">
      <a
        href={googleHref}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-[13px] text-[var(--text-dim,#475569)] transition hover:text-[var(--text,#0f172a)] hover:underline"
      >
        <Search className="h-3.5 w-3.5" strokeWidth={2} />
        Search Google for this firm
      </a>
    </div>
  );
}
