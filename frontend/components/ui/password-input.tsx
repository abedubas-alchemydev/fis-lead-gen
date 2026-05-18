"use client";

import type { InputHTMLAttributes } from "react";
import { useState } from "react";
import clsx from "clsx";
import { Eye, EyeOff } from "lucide-react";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label: string;
};

export function PasswordInput({ className, label, ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-[var(--text-dim,#475569)]">{label}</span>
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          className={clsx(
            "w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 pr-12 text-sm text-[var(--text,#0f172a)] shadow-sm outline-none transition",
            "placeholder:text-[var(--text-muted,#94a3b8)]",
            "hover:border-[var(--border-2,rgba(30,64,175,0.16))]",
            "focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/15 focus:shadow-md focus:shadow-[var(--accent,#6366f1)]/5",
            className,
          )}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 flex w-12 items-center justify-center text-[var(--text-muted,#94a3b8)] transition hover:text-[var(--text-dim,#475569)] focus:text-[var(--accent,#6366f1)] focus:outline-none"
        >
          {visible ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
        </button>
      </div>
    </label>
  );
}
