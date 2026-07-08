"use client";

import type { Route } from "next";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ShareListView } from "@/components/share/share-list-view";
import { SharePasswordGate, type UnlockOutcome } from "@/components/share/share-password-gate";
import { ShareProfileView } from "@/components/share/share-profile-view";
import { ShareShield } from "@/components/share/share-shield";
import { Button } from "@/components/ui/button";
import { PageSpinner } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/use-toast";
import { ApiError } from "@/lib/api";
import {
  getPublicShare,
  getPublicShareItemProfile,
  getPublicShareItems,
  unlockPublicShare,
} from "@/lib/public-share-api";
import type {
  PublicShareItemRow,
  PublicShareProfileResponse,
} from "@/types/public-share";

// State machine for the public share viewer. The unlock cookie is HttpOnly —
// this client NEVER reads it; every phase is derived from fetch outcomes:
//
//   loading  — bootstrap in flight
//   locked   — meta says unlocked:false, or any protected call returned 401
//   unlocked — items loaded; ?item=<id> flips list ⇄ profile within the phase
//   revoked  — a 404 anywhere on the share (unknown/revoked link)
//   error    — network / 5xx; Retry re-runs the bootstrap
type SharePhase = "loading" | "locked" | "unlocked" | "revoked" | "error";

const ACCESS_EXPIRED_MESSAGE = "Your access expired — re-enter the password.";

export interface ShareClientProps {
  token: string;
}

export function ShareClient({ token }: ShareClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const toast = useToast();

  const [phase, setPhase] = useState<SharePhase>("loading");
  const [shareName, setShareName] = useState<string | null>(null);
  const [items, setItems] = useState<PublicShareItemRow[]>([]);

  // Per-item profile state. Profiles are fetched lazily on first open and
  // cached for the life of the mount so list ⇄ profile hops are instant.
  const profileCache = useRef(new Map<number, PublicShareProfileResponse>());
  const [activeProfile, setActiveProfile] =
    useState<PublicShareProfileResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  // Bumped by the profile error surface's "Try again" so the fetch effect
  // re-runs for the same ?item value.
  const [profileAttempt, setProfileAttempt] = useState(0);

  // Monotonic run id: every bootstrap bumps it, and stale async continuations
  // (superseded bootstraps, unmount) check it before touching state.
  const bootstrapRunRef = useRef(0);

  const bootstrap = useCallback(async (): Promise<SharePhase> => {
    const run = ++bootstrapRunRef.current;
    setPhase("loading");
    setActiveProfile(null);
    setProfileError(null);

    let nextPhase: SharePhase;
    let nextName: string | null = null;
    let nextItems: PublicShareItemRow[] | null = null;

    try {
      const meta = await getPublicShare(token);
      nextName = meta.name;
      if (!meta.unlocked) {
        nextPhase = "locked";
      } else {
        // Unlocked according to meta — pull the rows. A 401 here means the
        // cookie died between the two calls (expired/rotated): fall back to
        // the gate rather than erroring.
        try {
          const response = await getPublicShareItems(token);
          nextName = response.name;
          nextItems = response.items;
          nextPhase = "unlocked";
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            nextPhase = "locked";
          } else if (err instanceof ApiError && err.status === 404) {
            nextPhase = "revoked";
          } else {
            nextPhase = "error";
          }
        }
      }
    } catch (err) {
      nextPhase =
        err instanceof ApiError && err.status === 404 ? "revoked" : "error";
    }

    if (bootstrapRunRef.current !== run) return nextPhase;
    if (nextName !== null) setShareName(nextName);
    if (nextItems !== null) setItems(nextItems);
    setPhase(nextPhase);
    return nextPhase;
  }, [token]);

  useEffect(() => {
    void bootstrap();
    return () => {
      // Invalidate any in-flight bootstrap on unmount / token change.
      bootstrapRunRef.current += 1;
    };
  }, [bootstrap]);

  const handleUnlock = useCallback(
    async (password: string): Promise<UnlockOutcome> => {
      try {
        await unlockPublicShare(token, password);
      } catch (err) {
        if (err instanceof ApiError) {
          if (err.status === 401 || err.status === 403) return "wrong";
          if (err.status === 429) return "cooldown";
          if (err.status === 404) {
            setPhase("revoked");
            return "revoked";
          }
        }
        return "error";
      }

      // 2xx — the browser stored the HttpOnly cookie. Re-bootstrap to pull
      // the item count + rows through the now-authorized path.
      const nextPhase = await bootstrap();
      if (nextPhase === "unlocked") return "ok";
      if (nextPhase === "revoked") return "revoked";
      // "locked" after a 200 unlock means the cookie didn't take (rotated
      // out immediately) — surface as a generic error, not "wrong password".
      return "error";
    },
    [token, bootstrap]
  );

  // ── ?item=<item_id> profile routing ────────────────────────────────────
  // Row clicks push history entries (browser Back returns to the list);
  // error corrections strip the param with replace so broken URLs don't
  // pollute history.
  const itemParam = searchParams.get("item");

  const clearItemParam = useCallback(() => {
    router.replace(`/share/${token}` as Route, { scroll: false });
  }, [router, token]);

  const activeItemId = useMemo(() => {
    if (itemParam === null) return null;
    const parsed = Number(itemParam);
    return Number.isInteger(parsed) ? parsed : Number.NaN;
  }, [itemParam]);

  const activeRow = useMemo(() => {
    if (activeItemId === null || Number.isNaN(activeItemId)) return null;
    return items.find((item) => item.item_id === activeItemId) ?? null;
  }, [activeItemId, items]);

  // Dedupe guard so the unknown-id toast fires once per bad param value
  // (the effect re-runs when items refresh, and StrictMode double-invokes
  // effects in dev).
  const handledBadParamRef = useRef<string | null>(null);

  useEffect(() => {
    if (phase !== "unlocked" || activeItemId === null) return;

    if (
      Number.isNaN(activeItemId) ||
      !items.some((item) => item.item_id === activeItemId)
    ) {
      if (handledBadParamRef.current !== itemParam) {
        handledBadParamRef.current = itemParam;
        toast.error("That item isn't part of this shared list.");
        clearItemParam();
      }
      return;
    }

    const cached = profileCache.current.get(activeItemId);
    if (cached) {
      setActiveProfile(cached);
      setProfileError(null);
      setProfileLoading(false);
      return;
    }

    let cancelled = false;
    setActiveProfile(null);
    setProfileError(null);
    setProfileLoading(true);

    getPublicShareItemProfile(token, activeItemId)
      .then((response) => {
        profileCache.current.set(activeItemId, response);
        if (cancelled) return;
        setActiveProfile(response);
      })
      .catch(async (err: unknown) => {
        if (cancelled) return;

        if (err instanceof ApiError && err.status === 401) {
          // Cookie expired mid-session — back to the gate. The ?item param
          // stays in the URL so a re-unlock returns to this profile.
          setPhase("locked");
          toast.info(ACCESS_EXPIRED_MESSAGE);
          return;
        }

        if (err instanceof ApiError && err.status === 404) {
          // The item left the share under us. Clear the param and refresh
          // the list; if the LIST now 404s too, the whole link is dead.
          toast.error("That item is no longer part of this shared list.");
          clearItemParam();
          try {
            const response = await getPublicShareItems(token);
            setShareName(response.name);
            setItems(response.items);
          } catch (refetchErr) {
            if (refetchErr instanceof ApiError && refetchErr.status === 404) {
              setPhase("revoked");
            } else if (
              refetchErr instanceof ApiError &&
              refetchErr.status === 401
            ) {
              setPhase("locked");
              toast.info(ACCESS_EXPIRED_MESSAGE);
            } else {
              setPhase("error");
            }
          }
          return;
        }

        setProfileError(
          err instanceof Error && err.message
            ? err.message
            : "Couldn't load this profile."
        );
      })
      .finally(() => {
        if (!cancelled) setProfileLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    phase,
    activeItemId,
    itemParam,
    items,
    token,
    profileAttempt,
    toast,
    clearItemParam,
  ]);

  const retryProfile = useCallback(() => {
    if (activeItemId === null || Number.isNaN(activeItemId)) return;
    profileCache.current.delete(activeItemId);
    setProfileError(null);
    setProfileAttempt((attempt) => attempt + 1);
  }, [activeItemId]);

  let content: JSX.Element;
  switch (phase) {
    case "loading":
      content = <PageSpinner label="Loading shared list" />;
      break;
    case "locked":
      content = (
        <SharePasswordGate shareName={shareName} onUnlock={handleUnlock} />
      );
      break;
    case "revoked":
      content = (
        <StatusCard
          title="This link is no longer active."
          body="Ask your DOX contact for a fresh link."
        />
      );
      break;
    case "error":
      content = (
        <StatusCard
          title="Something went wrong."
          body="We couldn't load this shared list. Check your connection and try again."
          action={
            <Button type="button" onClick={() => void bootstrap()}>
              Retry
            </Button>
          }
        />
      );
      break;
    case "unlocked":
      content = activeRow ? (
        <ShareProfileView
          token={token}
          row={activeRow}
          // Guard against a stale profile flashing while switching items.
          profile={
            activeProfile && activeProfile.item_id === activeRow.item_id
              ? activeProfile
              : null
          }
          loading={profileLoading}
          error={profileError}
          onRetry={retryProfile}
        />
      ) : (
        <ShareListView
          token={token}
          shareName={shareName ?? "Shared lead list"}
          items={items}
        />
      );
      break;
  }

  return (
    <>
      {/* Watermark + input guards from FIRST paint — the gate included. */}
      <ShareShield token={token} shareName={shareName} />
      {content}
    </>
  );
}

// Terminal-state card shared by the revoked and error screens.
function StatusCard({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: JSX.Element;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-4 py-10">
      <div
        className="w-full max-w-[420px] rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8 text-center"
        style={{
          boxShadow:
            "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
        }}
      >
        <h1 className="text-lg font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
          {title}
        </h1>
        <p className="mt-2 text-sm text-[var(--text-dim,#475569)]">{body}</p>
        {action ? <div className="mt-5">{action}</div> : null}
      </div>
    </div>
  );
}
