"use client";

import { ArrowLeft } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

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
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => router.back()}
        aria-label="Go back to the previous page"
      >
        <ArrowLeft className="h-4 w-4" strokeWidth={2} aria-hidden />
        Back
      </Button>
    </div>
  );
}
