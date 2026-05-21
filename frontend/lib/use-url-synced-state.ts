"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Route } from "next";
import {
  type ReadonlyURLSearchParams,
  useRouter,
  useSearchParams,
} from "next/navigation";

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

  const [state, setState] = useState<T>(urlState);

  // External navigation (Back/Forward, share-link) → adopt the URL. Our own
  // commits serialize back to `urlString`, so this no-ops for them.
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
