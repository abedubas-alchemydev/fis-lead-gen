"use client";

import * as React from "react";
import clsx from "clsx";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline";
};

export function Button({ className, variant = "primary", ...props }: ButtonProps) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center rounded-xl px-5 py-3 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30 disabled:cursor-not-allowed disabled:opacity-60",
        variant === "primary"
          ? "bg-[var(--accent,#6366f1)] text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 hover:bg-[var(--accent-2,#8b5cf6)] hover:shadow-xl hover:shadow-[var(--accent,#6366f1)]/25"
          : "border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)] shadow-sm hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)] hover:shadow-md",
        className,
      )}
      {...props}
    />
  );
}
