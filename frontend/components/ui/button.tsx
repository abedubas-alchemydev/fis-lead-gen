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
        "inline-flex items-center justify-center rounded-xl px-5 py-3 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-blue/30 disabled:cursor-not-allowed disabled:opacity-60",
        variant === "primary"
          ? "bg-navy text-white shadow-lg shadow-navy/15 hover:bg-[#112b54] hover:shadow-xl hover:shadow-navy/20"
          : "border border-slate-200 bg-white text-navy shadow-sm hover:border-slate-300 hover:bg-slate-50 hover:shadow-md",
        className,
      )}
      {...props}
    />
  );
}
