"use client";

import * as React from "react";
import clsx from "clsx";

export type ButtonVariant = "primary" | "outline" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg" | "icon";

// Base classes shared by every button — layout, focus ring, disabled state.
// Geometry (height, padding, radius, text size) lives in `buttonSizes` so the
// whole app draws buttons from one scale.
export const buttonBase =
  "inline-flex items-center justify-center font-semibold transition focus:outline-none focus:ring-2 focus:ring-[var(--accent,#6366f1)]/30 disabled:cursor-not-allowed disabled:opacity-60";

// The single source of truth for button size. `md` (40px tall) is the default
// and what the vast majority of buttons should use; `sm` (32px) is for dense
// rows/toolbars, `lg` (48px) for hero CTAs, `icon` for square icon-only buttons.
export const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 rounded-lg px-3 text-[13px]",
  md: "h-10 gap-2 rounded-xl px-4 text-sm",
  lg: "h-12 gap-2 rounded-xl px-6 text-base",
  icon: "h-10 w-10 rounded-xl",
};

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--accent,#6366f1)] text-white shadow-lg shadow-[var(--accent,#6366f1)]/20 hover:bg-[var(--accent-2,#8b5cf6)] hover:shadow-xl hover:shadow-[var(--accent,#6366f1)]/25",
  outline:
    "border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] text-[var(--text,#0f172a)] shadow-sm hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)] hover:shadow-md",
  ghost:
    "border border-transparent text-[var(--text-dim,#475569)] hover:bg-[var(--surface-2,#f1f6fd)]",
  danger:
    "bg-[var(--red,#dc2626)] text-white shadow-sm shadow-[rgba(220,38,38,0.2)] hover:brightness-110",
};

// Build the standard button class string. Exported so the rare button that must
// stay a raw <button>/<a> (custom background, brand chrome) can still match the
// app-wide size and shape without duplicating the scale.
export function buttonClass(opts?: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}): string {
  const { variant = "primary", size = "md", className } = opts ?? {};
  return clsx(buttonBase, buttonSizes[size], buttonVariants[variant], className);
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonProps) {
  return (
    <button className={buttonClass({ variant, size, className })} {...props} />
  );
}
