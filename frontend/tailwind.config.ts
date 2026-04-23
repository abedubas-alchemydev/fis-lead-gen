import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      // Add the opacity stops the dashboard-redesign.html mockup uses —
      // Tailwind's default scale is {0,5,10,20,25,30,...}, so /12 and /15
      // silently compile to nothing unless we explicitly add them here.
      // Affects every bg-*/12, text-*/15, border-*/12, etc. in the app,
      // but these classes were producing NO CSS before — so this is a
      // pure bug-fix, not a visual override.
      opacity: {
        12: "0.12",
        15: "0.15"
      },
      // Bind Tailwind's `font-sans` / `font-mono` utilities to the CSS
      // variables set by `next/font/google` in app/layout.tsx. Without
      // this, `font-sans` on <body> resolves to Tailwind's default
      // system-font stack and overrides the global `body { font-family }`
      // rule in globals.css, yielding different fonts across routes.
      fontFamily: {
        sans: ["var(--font-sans)", "Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "ui-monospace", "monospace"]
      },
      colors: {
        navy: "#0A1F3F",
        blue: "#1B5E9E",
        gold: "#E8A838",
        success: "#27AE60",
        warning: "#F39C12",
        danger: "#E74C3C",
        surface: "#ECF0F1",
        ink: "#10233F"
      },
      boxShadow: {
        shell: "0 18px 40px rgba(10, 31, 63, 0.12)"
      }
    }
  },
  plugins: []
};

export default config;

