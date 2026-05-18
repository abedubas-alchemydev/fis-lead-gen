import type { InputHTMLAttributes } from "react";
import clsx from "clsx";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
};

export function Input({ className, label, ...props }: InputProps) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-[var(--text-dim,#475569)]">{label}</span>
      <input
        className={clsx(
          "w-full rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 py-3 text-sm text-[var(--text,#0f172a)] shadow-sm outline-none transition",
          "placeholder:text-[var(--text-muted,#94a3b8)]",
          "hover:border-[var(--border-2,rgba(30,64,175,0.16))]",
          "focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/15 focus:shadow-md focus:shadow-[var(--accent,#6366f1)]/5",
          className,
        )}
        {...props}
      />
    </label>
  );
}
