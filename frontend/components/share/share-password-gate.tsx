"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { PasswordInput } from "@/components/ui/password-input";

// Outcome of one unlock attempt, produced by ShareClient's handler (which
// owns all fetching + the phase machine). The gate only maps outcomes to
// presentation:
//   ok / revoked → the parent flips phase, so this component unmounts
//   wrong        → inline error
//   cooldown     → 429 cooldown sub-state (form disabled + 60s timer)
//   error        → generic inline error (network / 5xx)
export type UnlockOutcome = "ok" | "wrong" | "cooldown" | "revoked" | "error";

export interface SharePasswordGateProps {
  shareName: string | null;
  onUnlock: (password: string) => Promise<UnlockOutcome>;
}

const COOLDOWN_SECONDS = 60;

const WRONG_PASSWORD_MESSAGE =
  "Incorrect password. Check the link owner's message and try again.";
const COOLDOWN_MESSAGE =
  "Too many attempts. Wait a few minutes before trying again.";
const GENERIC_ERROR_MESSAGE =
  "Something went wrong. Check your connection and try again.";

export function SharePasswordGate({ shareName, onUnlock }: SharePasswordGateProps) {
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // null = no cooldown; > 0 = counting down (form disabled, "Try again"
  // disabled); 0 = timer elapsed ("Try again" re-enabled, form still
  // disabled until the user explicitly re-arms it).
  const [cooldownRemaining, setCooldownRemaining] = useState<number | null>(null);

  const inCooldown = cooldownRemaining !== null;
  const formDisabled = pending || inCooldown;

  // A successful unlock re-bootstraps the parent, which unmounts this gate
  // while our awaited onUnlock is still resolving — guard the trailing
  // setState calls.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 1s tick for the cooldown countdown. Stops at 0 (button re-enables);
  // cleared automatically on unmount or when cooldown is dismissed.
  useEffect(() => {
    if (cooldownRemaining === null || cooldownRemaining <= 0) return;
    const timer = window.setTimeout(() => {
      setCooldownRemaining((s) => (s === null ? null : Math.max(0, s - 1)));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [cooldownRemaining]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (formDisabled) return;
    if (!password) {
      setError("Enter the access password.");
      return;
    }

    setPending(true);
    setError(null);
    const outcome = await onUnlock(password);
    if (!mountedRef.current) return;
    setPending(false);

    switch (outcome) {
      case "wrong":
        setError(WRONG_PASSWORD_MESSAGE);
        break;
      case "cooldown":
        setError(null);
        setCooldownRemaining(COOLDOWN_SECONDS);
        break;
      case "error":
        setError(GENERIC_ERROR_MESSAGE);
        break;
      case "ok":
      case "revoked":
        // Parent phase change unmounts the gate — nothing to render here.
        break;
    }
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4 py-10">
      <div className="w-full max-w-[420px]">
        <div
          className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-8"
          style={{
            boxShadow:
              "var(--shadow-card, 0 1px 2px rgba(15,23,42,0.04), 0 4px 14px rgba(15,23,42,0.05))",
          }}
        >
          {/* Static copy of the DOX [d] brand mark from the app sidebar
              (components/layout/app-shell.tsx). */}
          <div
            className="mx-auto grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[18px] font-extrabold text-white shadow-[0_6px_20px_rgba(10,31,63,0.35)]"
            style={{
              background: "linear-gradient(135deg, #0A1F3F, #1B5E9E)",
              fontFamily:
                "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif",
              letterSpacing: "-0.04em",
              lineHeight: 1,
            }}
            aria-hidden
          >
            d
          </div>

          <p className="mt-5 text-center text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            Shared lead list
          </p>
          <h1 className="mt-1 text-center text-xl font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            {shareName ?? "Lead list"}
          </h1>
          <p className="mt-2 text-center text-sm text-[var(--text-dim,#475569)]">
            Enter the password you received with this link to view the list.
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit} noValidate>
            <PasswordInput
              label="Access password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={formDisabled}
              autoFocus
              autoComplete="off"
              placeholder="Enter password"
            />

            {error ? (
              <div
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
              >
                {error}
              </div>
            ) : null}

            {inCooldown ? (
              <div
                role="alert"
                className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"
              >
                <p className="text-sm text-amber-800">{COOLDOWN_MESSAGE}</p>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={cooldownRemaining > 0}
                  onClick={() => setCooldownRemaining(null)}
                >
                  {cooldownRemaining > 0
                    ? `Try again in ${cooldownRemaining}s`
                    : "Try again"}
                </Button>
              </div>
            ) : null}

            <Button type="submit" className="w-full" disabled={formDisabled}>
              {pending ? "Unlocking…" : "Unlock list"}
            </Button>
          </form>
        </div>

        <p className="mt-4 text-center text-xs text-[var(--text-muted,#94a3b8)]">
          This list is confidential and was prepared for you. Please don&apos;t
          forward the link or password.
        </p>
      </div>
    </div>
  );
}
