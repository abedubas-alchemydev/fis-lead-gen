// Single source of truth for the site-wide credit line: product copyright +
// developer attribution. Dropped into every footer surface (authenticated app
// shell, auth screens, public share viewer, marketing landing) so the year and
// wording can never drift between them.
//
// SERVER component (no "use client"): pure static markup that ships no JS.
// `new Date().getFullYear()` is evaluated at request time, so the year rolls
// over on its own with no redeploy.
//
// Colours come from the shared `--text*` tokens (with light fallbacks) so the
// line reads correctly on the dark authenticated app *and* the light-pinned
// public surfaces.

type SiteCreditProps = {
  /** Applied to the wrapping element so each footer controls size/colour. */
  className?: string;
};

export function SiteCredit({ className }: SiteCreditProps) {
  const year = new Date().getFullYear();

  return (
    <span className={className}>
      &copy; {year} DOX. All rights reserved. &middot; Developed by{" "}
      <a
        href="https://alchemydev.io"
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium underline-offset-2 transition-colors hover:text-[var(--text,#0f172a)] hover:underline focus-visible:text-[var(--text,#0f172a)] focus-visible:underline focus-visible:outline-none"
      >
        AlchemyDev.io
      </a>
    </span>
  );
}
