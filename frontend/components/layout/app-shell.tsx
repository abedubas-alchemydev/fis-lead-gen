"use client";

import Link from "next/link";
import type { Route } from "next";
import type { ReactNode } from "react";
import { LogOut, ShieldCheck } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

type SessionUser = {
  name?: string | null;
  email?: string | null;
  role?: string | null;
};

const navigation = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/master-list", label: "Master List" },
  { href: "/alerts", label: "Alerts" },
  { href: "/export", label: "Export" },
  { href: "/settings", label: "Settings" }
] as const satisfies ReadonlyArray<{ href: Route; label: string }>;

export function AppShell({
  children,
  session
}: {
  children: ReactNode;
  session: { user: SessionUser };
}) {
  const pathname = usePathname();
  const router = useRouter();

  function isActivePath(href: string) {
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  async function handleSignOut() {
    await authClient.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="min-h-screen px-4 py-4 lg:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-2rem)] w-full overflow-hidden rounded-[34px] border border-white/70 bg-white/55 shadow-shell backdrop-blur">
        <aside className="hidden w-[256px] shrink-0 bg-navy px-6 py-7 text-white lg:flex lg:flex-col xl:w-[272px]">
          <div>
            <p className="text-xs uppercase tracking-[0.3em] text-white/55">Lead Engine</p>
            <h1 className="mt-4 text-2xl font-semibold leading-tight">Client Clearing Lead Gen Engine</h1>
          </div>
          <nav className="mt-12 space-y-2">
            {navigation.map((item) => {
              const isActive = isActivePath(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`block rounded-2xl px-4 py-3 text-sm transition ${
                    isActive ? "bg-white text-navy" : "text-white/75 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="mt-auto rounded-[26px] border border-white/10 bg-white/10 p-4 backdrop-blur">
            <p className="text-xs uppercase tracking-[0.26em] text-white/55">User Role</p>
            <div className="mt-3 flex items-center gap-3">
              <div className="rounded-2xl bg-white/15 p-2">
                <ShieldCheck className="h-4 w-4" />
              </div>
              <div>
                <p className="font-medium">{session.user.name ?? "Authenticated User"}</p>
                <p className="text-sm capitalize text-white/70">{session.user.role ?? "viewer"}</p>
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-h-full min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200/80 bg-white/70 px-5 py-4 backdrop-blur xl:px-7">
            <div className="min-w-0 flex-1">
              <p className="text-xs uppercase tracking-[0.28em] text-blue">Enterprise Dashboard</p>
              <h2 className="mt-1 text-xl font-semibold text-navy">Lead Intelligence Workspace</h2>
            </div>
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-3">
              <div className="hidden max-w-[280px] rounded-2xl border border-slate-200 bg-white px-4 py-2 text-right sm:block">
                <p className="truncate text-sm font-medium text-navy">{session.user.name ?? "Authenticated User"}</p>
                <p className="truncate text-xs text-slate-500">{session.user.email ?? "Session active"}</p>
              </div>
              <Button variant="outline" onClick={handleSignOut} className="shrink-0">
                <LogOut className="mr-2 h-4 w-4" />
                Sign out
              </Button>
            </div>
          </header>
          <nav className="flex gap-2 overflow-x-auto border-b border-slate-200/80 bg-white/80 px-4 py-3 lg:hidden">
            {navigation.map((item) => {
              const isActive = isActivePath(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`whitespace-nowrap rounded-full px-4 py-2 text-sm ${
                    isActive ? "bg-navy text-white" : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <main className="min-w-0 flex-1 overflow-auto p-5 lg:p-6 xl:p-7">{children}</main>
        </div>
      </div>
    </div>
  );
}
