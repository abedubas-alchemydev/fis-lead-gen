"use client";

import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { apiRequest } from "@/lib/api";
import { authClient } from "@/lib/auth-client";

// ─── Sidebar nav icons — verbatim SVG paths from dashboard-redesign.html ──
// Each accepts className + strokeWidth so the SidebarNavLink can size + stroke
// them consistently. Using inline SVGs (not a third-party icon set) so the
// geometry matches the mockup exactly.

type IconProps = { className?: string; strokeWidth?: number };

function IconBase({
  className,
  strokeWidth = 2,
  children
}: IconProps & { children: ReactNode }) {
  // Mockup SVGs don't set stroke-linecap/linejoin — defaults are butt/miter.
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      className={className}
      aria-hidden
    >
      {children}
    </svg>
  );
}

function DashboardIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </IconBase>
  );
}

function MasterListIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M3 6h18M3 12h18M3 18h18" />
    </IconBase>
  );
}

// Briefcase + chart-bar mark — distinguishes the Investment Advisor list
// from the Master List's three-line glyph at a glance.
function AdvisorListIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
      <path d="M8 14v3" />
      <path d="M12 12v5" />
      <path d="M16 10v7" />
    </IconBase>
  );
}

function AlertsIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.7 21a2 2 0 01-3.4 0" />
    </IconBase>
  );
}

// Trending-up + dollar mark — distinguishes the Investors tab (SEC
// Form 4 insider transaction feed) from the BD-scoped Alerts bell at a
// glance. Two ascending bars + a dollar sign overlay.
function InvestorsIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M3 17l6-6 4 4 7-7" />
      <path d="M14 8h7v7" />
    </IconBase>
  );
}

function EmailExtractorIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M4 4h16v16H4z" />
      <path d="M4 8l8 5 8-5" />
    </IconBase>
  );
}

function ExportIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <path d="M7 10l5 5 5-5" />
      <path d="M12 15V3" />
    </IconBase>
  );
}

function SettingsIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h0a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51h0a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v0a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </IconBase>
  );
}

function FavoritesIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M12 2l2.9 6.3 6.9.7-5.2 4.7 1.5 6.8L12 17l-6.1 3.5 1.5-6.8L2.2 9l6.9-.7L12 2z" />
    </IconBase>
  );
}

function VisitedFirmsIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </IconBase>
  );
}

function VaultIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <rect x="3" y="10" width="18" height="11" rx="2" />
      <path d="M7 10V7a5 5 0 0110 0v3" />
    </IconBase>
  );
}

function UsersIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2 21c0-3.5 3.1-6 7-6s7 2.5 7 6" />
      <circle cx="17" cy="8" r="2.5" />
      <path d="M16 14c2.8 0 5 1.8 5 4" />
    </IconBase>
  );
}

type SessionUser = {
  name?: string | null;
  email?: string | null;
  role?: string | null;
};

type StatsLite = {
  total_active_bds: number;
  deficiency_alerts: number;
};

type BadgeKey = "total" | "alerts" | null;

type NavIconComponent = (props: IconProps) => JSX.Element;

type NavEntry = {
  href: Route;
  label: string;
  icon: NavIconComponent;
  badgeKey: BadgeKey;
  adminOnly?: boolean;
};

const workspaceNav: ReadonlyArray<NavEntry> = [
  { href: "/dashboard", label: "Dashboard", icon: DashboardIcon, badgeKey: null },
  { href: "/master-list", label: "Master List", icon: MasterListIcon, badgeKey: "total" },
  // ``as Route`` cast: Next's typed-routes type-gen runs on `next build`/`next
  // dev`, so a fresh route added in a tsc-only check (no Next pipeline run)
  // isn't yet known to the Route union. Removing once a build regenerates
  // .next/types is fine but cheap to leave for new sibling routes.
  { href: "/advisor-list" as Route, label: "Investment Advisors", icon: AdvisorListIcon, badgeKey: null },
  { href: "/alerts", label: "Alerts", icon: AlertsIcon, badgeKey: "alerts" },
  { href: "/investors" as Route, label: "Investors", icon: InvestorsIcon, badgeKey: null },
  { href: "/email-extractor", label: "Email Extractor", icon: EmailExtractorIcon, badgeKey: null },
  { href: "/export", label: "Export", icon: ExportIcon, badgeKey: null },
  { href: "/my-favorites", label: "My Favorites", icon: FavoritesIcon, badgeKey: null },
  { href: "/visited-firms", label: "Visited Firms", icon: VisitedFirmsIcon, badgeKey: null }
];

const accountNav: ReadonlyArray<NavEntry> = [
  { href: "/settings", label: "Settings", icon: SettingsIcon, badgeKey: null },
  { href: "/settings/users" as Route, label: "Users", icon: UsersIcon, badgeKey: null, adminOnly: true },
  { href: "/vault", label: "Vault", icon: VaultIcon, badgeKey: null }
];

function initialsFromName(name: string | null | undefined): string {
  const cleaned = (name ?? "").trim();
  if (!cleaned) return "AE";
  const words = cleaned.split(/\s+/).filter((w) => w.length > 0);
  if (words.length === 0) return "AE";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}

export function AppShell({
  children,
  session
}: {
  children: ReactNode;
  session: { user: SessionUser };
}) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  // Sidebar Master List link href. When the user is already on
  // /master-list, preserve the live filter / sort / page query string so
  // clicking the nav item is a no-op rather than a destructive reset.
  // From any other route, link to bare /master-list (the user has no
  // master-list filter context to preserve).
  const masterListHref = useMemo<Route>(() => {
    if (pathname === "/master-list") {
      const qs = searchParams.toString();
      return (qs ? `/master-list?${qs}` : "/master-list") as Route;
    }
    return "/master-list" as Route;
  }, [pathname, searchParams]);
  const [stats, setStats] = useState<StatsLite | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await authClient.signOut();
    } catch {
      // BetterAuth swallows errors with a thrown Response in some
      // codepaths. Either way, the cookie is cleared client-side and
      // the next gated page hit will redirect to /login. So we still
      // navigate even if signOut() throws.
    }
    router.push("/login");
    router.refresh();
  }

  // One-shot stats fetch for sidebar badges. Silent on failure — badges
  // simply don't render, which is the graceful degraded state.
  useEffect(() => {
    let active = true;
    apiRequest<StatsLite>("/api/v1/stats")
      .then((s) => {
        if (active) setStats(s);
      })
      .catch(() => {
        /* swallow — badges stay hidden */
      });
    return () => {
      active = false;
    };
  }, []);

  const badges: Record<Exclude<BadgeKey, null>, string | null> = {
    total: stats ? stats.total_active_bds.toLocaleString() : null,
    alerts: stats && stats.deficiency_alerts > 0 ? stats.deficiency_alerts.toString() : null
  };

  const initials = initialsFromName(session.user.name ?? session.user.email);

  const role = session.user.role ?? "viewer";
  const displayRole = role.charAt(0).toUpperCase() + role.slice(1);
  const isAdmin = role === "admin";

  const visibleWorkspaceNav = workspaceNav.filter((e) => !e.adminOnly || isAdmin);
  const visibleAccountNav = accountNav.filter((e) => !e.adminOnly || isAdmin);

  function isActivePath(href: string) {
    if (pathname === href) return true;
    if (!pathname.startsWith(`${href}/`)) return false;
    // If a sibling nav entry has a longer matching href, defer to it. Stops
    // /settings from staying highlighted while on /settings/users.
    const all = [...visibleWorkspaceNav, ...visibleAccountNav];
    return !all.some(
      (e) =>
        e.href !== href &&
        e.href.length > href.length &&
        (pathname === e.href || pathname.startsWith(`${e.href}/`))
    );
  }

  return (
    <div className="h-screen overflow-hidden">
      <div className="flex h-full">
        {/* ═════════ SIDEBAR ═════════
            Colors use var(--token, fallback) so non-dashboard routes (no
            .dashboard-theme on html) keep their existing light-only look via
            fallbacks, while the dashboard route gets themed values. */}
        <aside className="hidden h-full w-[260px] shrink-0 flex-col border-r border-[var(--border,rgba(30,64,175,0.1))] bg-gradient-to-b from-[var(--sidebar-a,#ffffff)] to-[var(--sidebar-b,#ffffff)] px-4 py-6 backdrop-blur-[10px] lg:flex">
          {/* Brand */}
          <div className="mb-5 flex items-center gap-3 border-b border-[var(--border,rgba(30,64,175,0.1))] px-2.5 pb-6 pt-2">
            <div
              className="grid h-9 w-9 place-items-center rounded-[10px] text-[18px] font-extrabold text-white shadow-[0_6px_20px_rgba(10,31,63,0.35)]"
              style={{
                background: "linear-gradient(135deg, #0A1F3F, #1B5E9E)",
                fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif",
                letterSpacing: "-0.04em",
                lineHeight: 1
              }}
              aria-hidden
            >
              d
            </div>
            <div className="min-w-0">
              <div className="truncate text-[15px] font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
                DOX
              </div>
              <div className="truncate text-[11px] uppercase tracking-[0.04em] text-[var(--text-muted,#94a3b8)]">
                Institutional Finance Intelligence
              </div>
            </div>
          </div>

          {/* Workspace section */}
          <SidebarSectionLabel>Workspace</SidebarSectionLabel>
          <nav className="flex flex-col" aria-label="Workspace">
            {visibleWorkspaceNav.map((entry) => {
              const live =
                entry.href === "/master-list"
                  ? { ...entry, href: masterListHref }
                  : entry;
              return (
                <SidebarNavLink
                  key={entry.href}
                  entry={live}
                  active={isActivePath(entry.href)}
                  badge={entry.badgeKey ? badges[entry.badgeKey] : null}
                />
              );
            })}
          </nav>

          {/* Account section */}
          <SidebarSectionLabel>Account</SidebarSectionLabel>
          <nav className="flex flex-col" aria-label="Account">
            {visibleAccountNav.map((entry) => (
              <SidebarNavLink
                key={entry.href}
                entry={entry}
                active={isActivePath(entry.href)}
                badge={null}
              />
            ))}
          </nav>

          {/* User card — pinned to bottom of sidebar. Matches mockup
              .user-card exactly: avatar + user-name + user-role, plus a
              sign-out icon button on the right (added 2026-05-04). */}
          <div className="mt-auto flex items-center gap-3 rounded-[14px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] p-3.5">
            <div
              className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-full text-[14px] font-bold text-white"
              style={{ background: "linear-gradient(135deg, #10b981, #06b6d4)" }}
            >
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-semibold text-[var(--text,#0f172a)]">
                {session.user.name ?? "Authenticated User"}
              </div>
              <div className="truncate text-[11px] text-[var(--text-muted,#94a3b8)]">
                {displayRole} · DOX Clearing
              </div>
            </div>
            <button
              type="button"
              onClick={handleSignOut}
              disabled={signingOut}
              aria-label="Sign out"
              title="Sign out"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-white text-[var(--text-muted,#94a3b8)] transition hover:bg-[#f1f6fd] hover:text-[var(--text,#0f172a)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {/* Door-with-arrow "sign out" icon. Matches the bell/search
                  16-18px stroke-2 family used elsewhere in the topbar. */}
              <svg
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                viewBox="0 0 24 24"
                aria-hidden
              >
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </button>
          </div>
        </aside>

        {/* ═════════ MAIN COLUMN ═════════
            .canvas-surface renders solid #eaf3ff for non-dashboard routes,
            and the mockup's radial-glow gradient (using --body-glow-* +
            --bg) when .dashboard-theme is active on <html>. */}
        <div className="canvas-surface flex h-full min-w-0 flex-1 flex-col">
          {/* Mobile pill nav (visible below lg) — desktop has the sidebar instead. */}
          <nav
            className="flex shrink-0 gap-2 overflow-x-auto border-b border-slate-200/70 bg-white/80 px-4 py-3 lg:hidden"
            aria-label="Primary mobile"
          >
            {[...visibleWorkspaceNav, ...visibleAccountNav].map(({ href, label }) => {
              const active = isActivePath(href);
              const liveHref = href === "/master-list" ? masterListHref : href;
              return (
                <Link
                  key={href}
                  href={liveHref}
                  className={`whitespace-nowrap rounded-full px-4 py-2 text-sm ${
                    active ? "bg-indigo-500/15 text-indigo-600" : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          {/* Scrollable content area — each page renders its own topbar row
              (crumbs + title + TopActions) matching the mockup's `.topbar`
              inside `.main`. */}
          <main className="min-w-0 flex-1 overflow-auto">{children}</main>
        </div>
      </div>
    </div>
  );
}

function SidebarSectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 pb-2 pt-3.5 text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted,#94a3b8)]">
      {children}
    </div>
  );
}

function SidebarNavLink({
  entry,
  active,
  badge
}: {
  entry: NavEntry;
  active: boolean;
  badge: string | null;
}) {
  const Icon = entry.icon;

  return (
    <Link
      href={entry.href}
      aria-current={active ? "page" : undefined}
      style={active ? { background: "var(--nav-active-bg, rgba(37,99,235,0.12))" } : undefined}
      className={`relative flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-[13.5px] font-medium transition ${
        active
          ? "text-[var(--nav-active-text,#312e81)] shadow-[inset_0_0_0_1px_rgba(99,102,241,0.3)]"
          : "text-[var(--text-dim,#475569)] hover:bg-[var(--nav-hover,rgba(15,23,42,0.04))] hover:text-[var(--text,#0f172a)]"
      }`}
    >
      {active ? (
        <span
          aria-hidden
          className="absolute -left-4 bottom-2 top-2 w-[3px] rounded-r-[3px]"
          style={{ background: "linear-gradient(180deg, #6366f1, #8b5cf6)" }}
        />
      ) : null}
      <Icon className="h-[18px] w-[18px] opacity-90" strokeWidth={2} />
      <span className="flex-1 truncate">{entry.label}</span>
      {badge ? (
        // .badge spec: padding 2px 8px, 11px, font-weight 600, 1px border,
        // rounded 999px. Mockup uses the red-emphasis variant for both
        // Master List and Alerts badges; `var(--pill-red-text)` swaps to
        // #fca5a5 in dark mode automatically.
        <span className="rounded-full border border-red-500/30 bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-[var(--pill-red-text,#b91c1c)]">
          {badge}
        </span>
      ) : null}
    </Link>
  );
}
