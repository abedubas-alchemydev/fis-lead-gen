"use client";

import { useEffect, useRef } from "react";
import { usePathname, useSearchParams } from "next/navigation";

import { enqueueActivity, type ActivityMeta } from "./tracker";

// One ``nav_view`` per pathname/search change, plus a paired
// ``search_query`` event when ``?q=`` is present. We only log the
// length of the query, never the text itself — see the FE→server
// scrubber in app/api/activity/events/route.ts for the corresponding
// allowlist on the write side.

export function RouteObserver() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const lastPathRef = useRef<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!pathname) return;

    const filterKeys = Array.from(searchParams.keys());
    const meta: ActivityMeta = {};
    if (lastPathRef.current && lastPathRef.current !== pathname) {
      meta.from_path = lastPathRef.current;
    }
    if (filterKeys.length > 0) meta.filter_keys = filterKeys;
    enqueueActivity(
      "nav_view",
      pathname,
      Object.keys(meta).length > 0 ? meta : null,
    );

    const q = searchParams.get("q");
    if (q && q.length > 0) {
      enqueueActivity("search_query", pathname, {
        query_present: true,
        query_length: q.length,
        filter_keys: filterKeys,
      });
    }

    lastPathRef.current = pathname;
  }, [pathname, searchParams]);

  return null;
}
