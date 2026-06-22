import { headers } from "next/headers";
import { KeyRound } from "lucide-react";

import { ChangePasswordForm } from "@/components/settings/change-password-form";
import { OutreachSignatureForm } from "@/components/settings/outreach-signature-form";
import { auth } from "@/lib/auth";
import { getRequiredSession } from "@/lib/auth-server";

const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  microsoft: "Microsoft",
  yahoo: "Yahoo",
};

export default async function AccountSettingsPage() {
  await getRequiredSession();

  const accounts = await auth.api.listUserAccounts({ headers: await headers() });
  const hasPassword = accounts.some((a) => a.providerId === "credential");
  const oauthProviders = accounts
    .map((a) => a.providerId)
    .filter((id): id is string => typeof id === "string" && id !== "credential");
  const providerLabel =
    oauthProviders.length > 0
      ? PROVIDER_LABELS[oauthProviders[0]] ?? oauthProviders[0]
      : "your identity provider";

  return (
    <div className="px-4 sm:px-7 pb-12 pt-7 lg:px-9">
      <header className="mb-7 max-w-2xl">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
          My account
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          Change your password
        </h1>
        <p className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
          Update the password you use to sign in. Changing your password will sign you out of every other
          device you&apos;re signed in on.
        </p>
      </header>

      <section className="max-w-xl rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-7 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
        {hasPassword ? (
          <ChangePasswordForm />
        ) : (
          <div className="flex items-start gap-4">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--surface-2,#f1f6fd)] text-[var(--accent,#6366f1)]">
              <KeyRound className="h-5 w-5" aria-hidden />
            </div>
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
                No local password
              </p>
              <h2 className="text-lg font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
                You sign in with {providerLabel}
              </h2>
              <p className="max-w-md text-[13px] leading-5 text-[var(--text-dim,#475569)]">
                You don&apos;t have a password set on this account. To set one, sign out and use{" "}
                <span className="font-medium text-[var(--text,#0f172a)]">Forgot password</span> on the sign-in
                page &mdash; we&apos;ll email you a link to choose a password. After that you can change it
                here anytime.
              </p>
            </div>
          </div>
        )}
      </section>

      <section className="mt-8 max-w-xl rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-7 shadow-[var(--shadow-card,0_1px_2px_rgba(15,23,42,0.04),0_4px_14px_rgba(15,23,42,0.05))]">
        <header className="mb-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]">
            Outreach
          </p>
          <h2 className="mt-1 text-lg font-bold tracking-[-0.01em] text-[var(--text,#0f172a)]">
            Email signature
          </h2>
          <p className="mt-2 text-[13px] leading-5 text-[var(--text-dim,#475569)]">
            A footer appended beneath the body of the outreach emails you send.
          </p>
        </header>
        <OutreachSignatureForm />
      </section>
    </div>
  );
}
