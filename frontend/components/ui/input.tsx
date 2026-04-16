import type { InputHTMLAttributes } from "react";
import clsx from "clsx";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
};

export function Input({ className, label, ...props }: InputProps) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      <input
        className={clsx(
          "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm outline-none transition",
          "placeholder:text-slate-400",
          "hover:border-slate-300",
          "focus:border-blue focus:ring-2 focus:ring-blue/15 focus:shadow-md focus:shadow-blue/5",
          className,
        )}
        {...props}
      />
    </label>
  );
}
