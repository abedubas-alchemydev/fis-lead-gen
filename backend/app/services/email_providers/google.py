"""Google (Gmail API) email provider.

Thin adapter over the existing :mod:`app.services.gmail_sender` and
:mod:`app.services.google_oauth` modules so the outreach endpoint can
dispatch via :class:`EmailProvider` without those modules needing to
change shape. Provider-specific exceptions are re-raised as the
shared package exceptions so the endpoint's 412 / 502 / 503 mapping
stays provider-agnostic.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.email_providers.base import (
    EmailAccountNotLinked,
    EmailProviderConfigurationError,
    EmailScopeRequired,
    EmailSendError,
)
from app.services.gmail_sender import (
    GMAIL_SEND_SCOPE,
    GmailScopeRequired,
    GmailSendError,
    send_gmail,
)
from app.services.google_oauth import (
    GoogleAccountNotLinked,
    GoogleOAuthConfigurationError,
    get_fresh_google_access_token,
)


class GoogleEmailProvider:
    provider_id: str = "google"
    send_scope: str = GMAIL_SEND_SCOPE

    async def get_fresh_token(
        self, db: AsyncSession, user_id: str
    ) -> tuple[str, list[str]]:
        try:
            return await get_fresh_google_access_token(db=db, user_id=user_id)
        except GoogleAccountNotLinked as exc:
            raise EmailAccountNotLinked(str(exc)) from exc
        except GoogleOAuthConfigurationError as exc:
            raise EmailProviderConfigurationError(str(exc)) from exc

    async def send(
        self,
        *,
        access_token: str,
        sender_email: str,
        to_email: str,
        subject: str,
        body: str,
    ) -> str:
        try:
            return await send_gmail(
                access_token=access_token,
                sender_email=sender_email,
                to_email=to_email,
                subject=subject,
                body=body,
            )
        except GmailScopeRequired as exc:
            raise EmailScopeRequired(str(exc)) from exc
        except GmailSendError as exc:
            raise EmailSendError(str(exc)) from exc
