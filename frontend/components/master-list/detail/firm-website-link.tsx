import { Globe } from "lucide-react";

// Renders the firm's website as a clickable row directly under the
// firm-name h1 on /master-list/{id}. Pure presentational — no client
// state, no fetches. When `website` is null/empty, this commit returns
// null so the slot stays empty for the Google-fallback follow-up.
export function FirmWebsiteLink({
  firmName: _firmName,
  website,
}: {
  firmName: string;
  website: string | null;
}) {
  const trimmed = (website ?? "").trim();
  if (!trimmed) return null;

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
