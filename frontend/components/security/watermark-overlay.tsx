"use client";

import { useEffect, useState } from "react";

export interface WatermarkOverlayProps {
  email: string;
  userId: string;
  name?: string;
}

function formatTimestamp(d: Date): string {
  const pad = (n: number) => n.toString().padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    ` ${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export function WatermarkOverlay({ email, userId, name }: WatermarkOverlayProps) {
  // Timestamp is captured on mount and refreshed every minute so a leaked
  // screenshot reveals approximately when it was taken without forcing a
  // repaint on every render.
  //
  // Initial state is intentionally an empty string instead of
  // ``formatTimestamp(new Date())``. The Next.js App Router server-side
  // renders this client component once, and the initial useState
  // function would run with the SERVER's clock; on hydration React then
  // re-renders with the CLIENT's clock and the timestamps differ by
  // seconds (and timezone). That mismatch surfaced as React errors
  // #425/#418/#423 on every page load. Seeding to empty + populating
  // inside useEffect (client-only) eliminates the mismatch; the
  // watermark briefly shows without the timestamp for one frame, which
  // is invisible at the overlay's 0.06 opacity.
  const [stamp, setStamp] = useState<string>("");

  useEffect(() => {
    setStamp(formatTimestamp(new Date()));
    const interval = window.setInterval(() => {
      setStamp(formatTimestamp(new Date()));
    }, 60_000);
    return () => window.clearInterval(interval);
  }, []);

  const label = `${name ?? email}  ·  ${userId.slice(0, 8)}  ·  ${stamp}`;

  return (
    <div
      data-security-overlay
      aria-hidden
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 2147483640,
        pointerEvents: "none",
        userSelect: "none",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "-50%",
          left: "-50%",
          width: "200%",
          height: "200%",
          transform: "rotate(-28deg)",
          transformOrigin: "center",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
          gap: "120px 80px",
          padding: "80px",
          opacity: 0.06,
          color: "var(--text, #0f172a)",
          fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, monospace)",
          fontSize: "13px",
          fontWeight: 600,
          letterSpacing: "0.04em",
          whiteSpace: "nowrap",
        }}
      >
        {Array.from({ length: 120 }).map((_, i) => (
          <span key={i}>{label}</span>
        ))}
      </div>
    </div>
  );
}
