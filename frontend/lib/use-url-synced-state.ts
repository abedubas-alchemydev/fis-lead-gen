"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Route } from "next";
import {
  type ReadonlyURLSearchParams,
  useRouter,
  useSearchParams,
} from "next/navigation";

// A frozen empty URLSearchParams typed as ReadonlyURLSearchParams so the
// hydration-safe initial parse() runs against the same shape useSearchParams
// returns. ReadonlyURLSearchParams is a structural subset of URLSearchParams
// (read-only methods only), so the cast is sound at runtime — only readonly
// methods are called by callers' parsers.
const EMPTY_SEARCH_PARAMS = new URLSearchParams() as unknown as ReadonlyURLSearchParams;

// URL-backed filter state with a synchronous local mirror.
//
// The page URL is the canonical store (share-links + Back/Forward), but
// `router.replace` is async — `useSearchParams` only updates once the
// navigation commits. When several edits fire faster than that round-trip
// (e.g. ticking multiple checkboxes in a multi-select), each handler reads
// the pre-edit URL and overwrites the previous edit, so only the last one
// survives. Mirroring the state in `useState` — which updates synchronously
// between events — lets edits accumulate. We reconcile with the URL by
// comparing canonical serialized strings via `build`, so our own commits
// round-trip back equal (no loop) while external navigation is adopted.
//
// Contract: `build` MUST be deterministic for a given state (stable key
// order, defaults stripped), and `parse`/`build` MUST be stable references
// (module-level functions) so the reconciliation effects don't re-fire every
// render.
export function useUrlSyncedState<T extends object>(
  parse: (sp: ReadonlyURLSearchParams) => T,
  build: (state: T) => string,
): {
  state: T;
  updateState: (patch: Partial<T>) => void;
  replaceState: (next: T) => void;
} {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlState = useMemo(() => parse(searchParams), [searchParams, parse]);
  const urlString = useMemo(() => build(urlState), [urlState, build]);

  // Seed state with `parse(empty params)` so the server-rendered HTML
  // and the client's first render agree regardless of the URL the page
  // was loaded with. Hydrating with URL-derived state directly used to
  // throw React #418 ("text content does not match server-rendered
  // HTML") on workspace pages opened via share-links / Back-nav,
  // because subtle render-timing differences between the SSR pass and
  // the hydration pass (PPR / route cache reuse / streaming) could
  // produce divergent text for the filter-derived UI (active count
  // chips, pre-filled inputs, selected segmented values). The mount
  // effect below upgrades state to the real `urlState` immediately on
  // the next client render — same pattern the watermark overlay uses
  // in components/security/watermark-overlay.tsx.
  const emptyState = useMemo<T>(
    () => parse(EMPTY_SEARCH_PARAMS),
    [parse],
  );
  const [state, setState] = useState<T>(emptyState);

  // First mount: upgrade defaults → real URL state.
  // Subsequent runs: adopt the URL on external navigation (Back/Forward,
  // share-link click). Our own commits serialize back to `urlString`, so
  // this no-ops for them.
  useEffect(() => {
    setState((prev) => (build(prev) === urlString ? prev : urlState));
  }, [urlString, urlState, build]);

  // Local edit diverged from the URL → push it. `replace` (not `push`) so
  // Back returns to the previous route, not the previous filter combo.
  useEffect(() => {
    const nextUrl = build(state);
    if (nextUrl !== urlString) {
      router.replace(nextUrl as Route, { scroll: false });
    }
  }, [state, urlString, build, router]);

  const updateState = useCallback((patch: Partial<T>) => {
    setState((prev) => ({ ...prev, ...patch }) as T);
  }, []);

  const replaceState = useCallback((next: T) => {
    setState(next);
  }, []);

  return { state, updateState, replaceState };
}
