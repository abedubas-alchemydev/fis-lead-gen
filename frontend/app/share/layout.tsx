import type { ReactNode } from "react";

import { Toaster } from "@/components/ui/toaster";

// Public chrome for /share/{token} — the DOX Share viewer surface. No app
// shell, no sidebar, no session: the visitor is an anonymous recipient of a
// shared lead list.
//
// The whole surface is pinned to the LIGHT theme (same pattern as the public
// landing page in app/page.tsx): the root layout's inline script stamps
// <html data-theme="dark"> from localStorage when a DOX user previously
// picked dark mode on this browser, which would flip the shared tokens and
// render this public page dark. The [data-theme="light"] scope re-asserts
// the light palette and the opaque --bg fill keeps the canvas consistent.
//
// <Toaster /> is mounted here because the module-level toast queue silently
// drops toasts when no Toaster is subscribed — and this surface toasts from
// both the share client (expired access, missing items) and the security
// input guards (blocked copy/print).
export default function ShareLayout({ children }: { children: ReactNode }) {
  return (
    <div
      data-theme="light"
      className="flex min-h-screen flex-col bg-[var(--bg,#eaf3ff)]"
    >
      <header className="border-b border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)]">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-4 py-3 sm:px-6">
          {/* Static copy of the DOX [d] brand mark from the app sidebar
              (components/layout/app-shell.tsx) — kept inline so this public
              route doesn't import the authenticated shell. */}
          <div
            className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] text-[18px] font-extrabold text-white shadow-[0_6px_20px_rgba(10,31,63,0.35)]"
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
          <div className="min-w-0">
            <p className="truncate text-sm font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
              Shared by DOX
            </p>
            <p className="truncate text-[11px] uppercase tracking-[0.04em] text-[var(--text-muted,#94a3b8)]">
              Institutional Finance Intelligence
            </p>
          </div>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t border-[var(--border,rgba(30,64,175,0.1))] py-5">
        <p className="mx-auto w-full max-w-5xl px-4 text-center text-xs text-[var(--text-muted,#94a3b8)] sm:px-6">
          Confidential — prepared for the intended recipient only.
        </p>
      </footer>

      <Toaster />
    </div>
  );
}
