"use client";

import { useCallback, useEffect, useState } from "react";

import { getLinkedProviders } from "@/lib/api";
import { authClient } from "@/lib/auth-client";
import type { EmailProviderId, LinkedProviderItem } from "@/lib/types";

// Settings -> Email Accounts: manage the OAuth accounts the user can
// send outreach from. A single user can link multiple accounts per
// provider (two Google accounts is the common case); the Outreach
// modal's "Send from" dropdown is populated from this page.

const PROVIDER_LABEL: Record<EmailProviderId, string> = {
  google: "Gmail",
  microsoft: "Outlook",
  yahoo: "Yahoo Mail"
};

// Per-provider send scopes mirror the BE checks (see
// outreach-modal.tsx for the same mapping). Requested at link time so
// the new account is immediately usable for sending.
const SEND_SCOPE_BY_PROVIDER: Record<EmailProviderId, string> = {
  google: "https://www.googleapis.com/auth/gmail.send",
  microsoft: "Mail.Send",
  yahoo: "mail-w"
};

export function EmailAccountsClient() {
  const [accounts, setAccounts] = useState<LinkedProviderItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyAccountId, setBusyAccountId] = useState<string | null>(null);
  const [linkingProvider, setLinkingProvider] = useState<EmailProviderId | null>(
    null
  );

  const refresh = useCallback(async () => {
    setLoadError(null);
    try {
      const result = await getLinkedProviders();
      setAccounts(result.items);
    } catch (err) {
      setLoadError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleLink(provider: EmailProviderId) {
    setActionError(null);
    setLinkingProvider(provider);
    const sendScope = SEND_SCOPE_BY_PROVIDER[provider];
    try {
      if (provider === "yahoo") {
        const maybeLink = (
          authClient as unknown as {
            linkOAuth2?: (args: {
              providerId: string;
              scopes?: string[];
              callbackURL?: string;
            }) => Promise<unknown>;
          }
        ).linkOAuth2;
        if (maybeLink) {
          await maybeLink({
            providerId: "yahoo",
            scopes: [sendScope],
            callbackURL: window.location.href
          });
        } else {
          // Fallback for older Better Auth versions whose generic-OAuth
          // client treats signIn.oauth2 as a link when already signed
          // in.
          await authClient.signIn.oauth2({
            providerId: "yahoo",
            callbackURL: window.location.href
          });
        }
      } else {
        await authClient.linkSocial({
          provider,
          scopes: [sendScope],
          callbackURL: window.location.href
        });
      }
    } catch (err) {
      setActionError(errorMessage(err));
      setLinkingProvider(null);
    }
    // No finally cleanup -- successful link triggers a redirect, and
    // the page reload picks up the new account.
  }

  async function handleUnlink(account: LinkedProviderItem) {
    setActionError(null);
    setBusyAccountId(account.account_id);
    try {
      const response = await fetch("/api/auth/unlink-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          providerId: account.provider,
          // Provider's external accountId (Google's ``sub``, MS Graph
          // object id, Yahoo GUID). Better Auth matches on
          // (providerId, accountId) so a user with two Google accounts
          // can drop just the one they pick instead of both.
          accountId: account.provider_account_id
        })
      });
      if (!response.ok) {
        const body = await safeJson(response);
        throw new Error(
          (body && typeof body === "object" && "message" in body
            ? String((body as Record<string, unknown>).message)
            : null) ?? `Unlink failed (status ${response.status}).`
        );
      }
      await refresh();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusyAccountId(null);
    }
  }

  if (loading) {
    return (
      <div className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-6 text-sm text-[var(--text-muted,#94a3b8)] shadow-sm">
        Loading your linked email accounts...
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-sm text-danger shadow-sm">
        {loadError}
      </div>
    );
  }

  const items = accounts ?? [];

  return (
    <div className="space-y-6">
      <header>
        <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
          Account
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-[-0.02em] text-[var(--text,#0f172a)]">
          Email accounts
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-dim,#475569)]">
          Outreach is sent from one of these accounts. Link more
          mailboxes to switch the &ldquo;Send from&rdquo; address on a
          per-email or per-service basis.
        </p>
      </header>

      <section className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-[var(--text,#0f172a)]">
          Linked accounts
        </h2>
        {items.length === 0 ? (
          <p className="mt-3 text-xs text-[var(--text-muted,#94a3b8)]">
            No accounts linked yet. Add one below.
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-[var(--border,rgba(30,64,175,0.1))]">
            {items.map((account) => (
              <li
                key={account.account_id}
                className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <p className="text-sm font-medium text-[var(--text,#0f172a)]">
                    {account.email_address ??
                      `${PROVIDER_LABEL[account.provider]} account`}
                  </p>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-muted,#94a3b8)]">
                    {PROVIDER_LABEL[account.provider]}
                    {account.has_send_scope ? null : (
                      <span className="ml-2 normal-case tracking-normal text-amber-700">
                        — send permission not granted yet
                      </span>
                    )}
                  </p>
                </div>
                <div className="flex gap-2">
                  {account.has_send_scope ? null : (
                    <button
                      type="button"
                      onClick={() => void handleLink(account.provider)}
                      disabled={linkingProvider === account.provider}
                      className="inline-flex h-9 items-center rounded-xl border border-amber-300 bg-amber-50 px-3 text-xs font-semibold text-amber-800 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {linkingProvider === account.provider
                        ? "Opening..."
                        : "Grant send access"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void handleUnlink(account)}
                    disabled={busyAccountId === account.account_id}
                    className="inline-flex h-9 items-center rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-white px-3 text-xs font-semibold text-[var(--text-dim,#475569)] transition hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {busyAccountId === account.account_id
                      ? "Unlinking..."
                      : "Unlink"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-2xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-[var(--text,#0f172a)]">
          Link another account
        </h2>
        <p className="mt-1 text-xs leading-5 text-[var(--text-muted,#94a3b8)]">
          You can link multiple accounts per provider. After consent,
          the new mailbox appears in the &ldquo;Send from&rdquo;
          dropdown.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {(
            ["google", "microsoft", "yahoo"] as const satisfies readonly EmailProviderId[]
          ).map((provider) => (
            <button
              key={provider}
              type="button"
              onClick={() => void handleLink(provider)}
              disabled={linkingProvider === provider}
              className="inline-flex h-10 items-center rounded-xl border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-4 text-sm font-medium text-[var(--text-dim,#475569)] transition hover:border-[var(--border-2,rgba(30,64,175,0.16))] hover:bg-[var(--surface-2,#f1f6fd)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {linkingProvider === provider
                ? `Opening ${PROVIDER_LABEL[provider]}...`
                : `Connect ${PROVIDER_LABEL[provider]}`}
            </button>
          ))}
        </div>
      </section>

      {actionError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-3 text-xs text-danger">
          {actionError}
        </div>
      ) : null}
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Unexpected error";
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}
