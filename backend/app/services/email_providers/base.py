"""Email-provider Protocol + shared exceptions.

Pulled out so :mod:`app.api.v1.endpoints.outreach` can dispatch on
``account.provider_id`` without importing provider-specific modules
and so each concrete provider in this package can raise the same
typed exceptions for the endpoint's 412 / 502 / 503 mapping.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


class EmailAccountNotLinked(RuntimeError):
    """No ``account`` row for this provider, or the refresh token is dead.

    Maps to ``412 <provider>_account_not_linked`` so the frontend can
    surface a "Connect <Provider>" CTA and trigger
    ``authClient.linkSocial`` (Google/Microsoft) or
    ``authClient.signIn.oauth2`` (Yahoo via the generic OAuth plugin).
    """


class EmailScopeRequired(RuntimeError):
    """Account linked but missing the send scope.

    Maps to ``412 <provider>_scope_required``. The FE prompts the same
    link flow with the send scope included.
    """


class EmailSendError(RuntimeError):
    """Provider's send API rejected the request for a non-auth reason.

    Maps to ``502 <provider>_api_error``. Examples: rate limited, mailbox
    over quota, recipient bounced fast-fail, SMTP transient.
    """


class EmailProviderConfigurationError(RuntimeError):
    """Provider client_id / client_secret are missing from env.

    Maps to ``503 <provider>_oauth_not_configured``. Surfaces when the
    deploy is missing the env values the OAuth refresh path needs.
    """


@runtime_checkable
class EmailProvider(Protocol):
    """Contract every transport in this package implements.

    Kept as a Protocol (not an abstract base class) so the concrete
    classes don't need to inherit — just match the shape. ``provider_id``
    is the discriminator stored on ``outreach_sends.provider`` and used
    as the dispatch key in :data:`PROVIDERS`.
    """

    provider_id: str
    send_scope: str
    """The OAuth scope required to send. Frontend uses this to drive the
    incremental-consent ``linkSocial`` call when a 412 *_scope_required
    is returned."""

    async def get_fresh_token(
        self, db: AsyncSession, account_id: str
    ) -> tuple[str, list[str]]:
        """Refresh and return ``(access_token, scopes)`` for the given account row.

        ``account_id`` is ``account.id`` (Better Auth's PK), NOT the
        provider's external user id (``account.account_id``). This lets
        a single user dispatch sends to a specific linked account when
        they have multiple of the same provider. The caller must verify
        row ownership before passing the id.

        Raises :class:`EmailAccountNotLinked` when the row is gone or
        the refresh token is revoked;
        :class:`EmailProviderConfigurationError` when the env config is
        missing. Concrete implementations may also raise the bare
        ``httpx`` errors for transient network failures, which the
        endpoint maps to 502 :class:`EmailSendError`.
        """
        ...

    async def send(
        self,
        *,
        access_token: str,
        sender_email: str,
        to_emails: list[str],
        subject: str,
        body: str,
        cc_emails: list[str] | None = None,
        bcc_emails: list[str] | None = None,
    ) -> str:
        """Send one email to all recipients and return a message id.

        ``to_emails`` is the visible primary recipient list (at least
        one). ``cc_emails`` are also visible to everyone; ``bcc_emails``
        are hidden — each transport delivers to BCC addresses while
        stripping the ``Bcc`` header from the transmitted/saved copy
        (Gmail strips it server-side, ``aiosmtplib.send_message`` removes
        it before DATA, and Graph models BCC natively via
        ``bccRecipients``). A single message is sent, so To/CC recipients
        see each other.

        Gmail returns the real Gmail message id. Microsoft Graph and
        Yahoo SMTP don't return an id we can rely on; those providers
        return a synthetic ``{provider_id}-{iso8601}`` placeholder so
        the audit row's ``gmail_message_id`` column always has
        something searchable.

        Raises :class:`EmailScopeRequired` when the provider rejects the
        send for a missing scope, :class:`EmailSendError` for any other
        provider failure.
        """
        ...
