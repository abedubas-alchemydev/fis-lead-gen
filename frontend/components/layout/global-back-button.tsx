"use client";

import { ArrowLeft } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

// Compact secondary-button styling — mirrors the back / secondary controls
// used elsewhere (e.g. the email-extractor scan page) so this global control
// looks native on every page and follows the active theme tokens.
const BACK_BTN =
  "inline-flex items-center justify-center gap-2 rounded-[10px] border border-[var(--border-2,rgba(30,64,175,0.16))] bg-[var(--surface,#ffffff)] px-3 py-1.5 text-[12px] font-medium text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)]";

/**
 * App-wide "Back" control, rendered once inside the persistent AppShell so it
 * appears top-left on every authenticated page.
 *
 * It returns the user to the previous page *and its state*: clicking calls
 * `router.back()`, so the browser restores the prior history entry's URL — our
 * list filters / sort / page all live in the query string — and its scroll
 * position.
 *
 * Visibility: hidden until the user has navigated within the app at least once.
 * AppShell is a layout subtree, so it persists across App-Router navigations
 * but remounts on a full page load. That means a fresh load / deep link / the
 * post-login landing starts with `canGoBack === false` and renders nothing —
 * there is no in-app page to go back to. The flag is monotonic: the first time
 * the pathname differs from where this component mounted, a back target exists.
 * We key on pathname (not the query string) so merely changing a list's filters
 * does not summon the button.
 */
export function GlobalBackButton(): React.ReactElement | null {
  const router = useRouter();
  const pathname = usePathname();
  const mountPathname = useRef(pathname);
  const [canGoBack, setCanGoBack] = useState(false);

  useEffect(() => {
    if (pathname !== mountPathname.current) {
      setCanGoBack(true);
    }
  }, [pathname]);

  if (!canGoBack) return null;

  return (
    <div className="px-7 pt-6 lg:px-9">
      <button
        type="button"
        onClick={() => router.back()}
        aria-label="Go back to the previous page"
        className={BACK_BTN}
      >
        <ArrowLeft className="h-4 w-4" strokeWidth={2} aria-hidden />
        Back
      </button>
    </div>
  );
}
