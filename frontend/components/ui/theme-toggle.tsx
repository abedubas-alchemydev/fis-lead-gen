"use client";

import { useEffect, useState } from "react";

import { Moon, Sun } from "lucide-react";

// Light/dark toggle for the topbar. Theme is driven by `data-theme="dark"`
// on <html> (matches the :root + [data-theme="dark"] scopes in globals.css)
// and persisted to localStorage under "leadEngineTheme" — the same key the
// pre-refactor dashboard useEffect used, so existing user preferences
// carry through.
type Theme = "light" | "dark";

export function ThemeToggle({ className = "" }: { className?: string }) {
  // Default to "light" so the first SSR render is deterministic; the effect
  // below hydrates from localStorage / existing DOM attr on mount. Any
  // mismatch resolves in one frame without a visible flash because the
  // theme tokens themselves live in CSS, not JS-rendered styles.
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem("leadEngineTheme");
    } catch {
      /* localStorage may be blocked */
    }
    const current = document.documentElement.getAttribute("data-theme");
    const next: Theme = stored === "dark" || current === "dark" ? "dark" : "light";
    setTheme(next);
    if (next === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    if (next === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    try {
      localStorage.setItem("leadEngineTheme", next);
    } catch {
      /* localStorage may be blocked */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      className={`grid h-[38px] w-[38px] place-items-center rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] hover:text-[var(--text,#0f172a)] ${className}`}
    >
      {theme === "dark" ? (
        <Sun className="h-[18px] w-[18px]" strokeWidth={2} />
      ) : (
        <Moon className="h-[18px] w-[18px]" strokeWidth={2} />
      )}
    </button>
  );
}
