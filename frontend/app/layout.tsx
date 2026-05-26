import type { ReactNode } from "react";
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "@/app/globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "DOX — Institutional Finance Intelligence",
  description: "Broker-dealer intelligence platform for clearing-services workflows."
};

// Inline, blocking script that runs before paint so the saved theme is
// applied on first render of every route — without it, `<ThemeToggle>`'s
// post-hydration effect was the only thing setting `data-theme`, which
// caused a light-mode flash on refresh and left dark mode broken on any
// route that didn't render the topbar (e.g. /auth/*).
const themeInitScript = `try{var t=localStorage.getItem('prospectEngineTheme');if(t==='dark')document.documentElement.setAttribute('data-theme','dark');}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  // suppressHydrationWarning: the inline <script> below mutates <html>
  // before hydration; without it React strips the data-theme attribute
  // during reconciliation, causing a dark→light flash on refresh.
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className={`${inter.variable} ${mono.variable} font-sans text-ink antialiased`}>{children}</body>
    </html>
  );
}
