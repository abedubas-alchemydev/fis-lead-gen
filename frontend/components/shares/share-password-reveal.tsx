"use client";

import clsx from "clsx";

import { CopyFieldButton } from "./copy-field-button";

// One-time reveal block for a share's URL + password. Reused by the result
// steps of the create-share modal and the rotate-password dialog. The
// password only exists in the create/rotate responses and can never be
// fetched again — hence the amber warning up top. Fields are read-only
// inputs (the input-guard layer always allows selection/copy inside INPUT
// elements) with explicit copy buttons alongside.
export function SharePasswordReveal({
  url,
  password,
}: {
  url: string;
  password: string;
}) {
  return (
    <div className="space-y-3">
      <p
        role="alert"
        className="rounded-md border border-amber-500/25 bg-amber-500/12 px-3 py-2 text-[12px] font-medium leading-5 text-amber-700"
      >
        Save this password now — it won&apos;t be shown again.
      </p>
      <RevealField label="Share URL" value={url} />
      <RevealField label="Password" value={password} mono />
    </div>
  );
}

function RevealField({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-[12px] font-medium text-[var(--text-dim,#475569)]">
        {label}
      </span>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="text"
          readOnly
          value={value}
          onFocus={(event) => event.currentTarget.select()}
          className={clsx(
            "w-full min-w-0 rounded-lg border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface-2,#f1f6fd)] px-3 py-2 text-[13px] text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:ring-2 focus:ring-[var(--accent,#6366f1)]/15",
            mono && "font-mono tracking-[0.02em]",
          )}
        />
        <CopyFieldButton value={value} label={label.toLowerCase()} />
      </div>
    </label>
  );
}
