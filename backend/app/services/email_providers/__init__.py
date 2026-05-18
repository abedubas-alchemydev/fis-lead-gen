"""Per-user email-send providers behind the Outreach modal.

Three transports are wired (Google / Microsoft / Yahoo); Apple is
intentionally absent because iCloud SMTP only accepts static
app-specific passwords, which breaks the OAuth + refresh-token pattern
every other provider here is built on.

The endpoint dispatches on ``account.provider_id`` so the user's choice
of "Continue with Google / Outlook / Yahoo" at login (plus any later
``linkSocial`` re-consent the Outreach modal triggers when a send scope
is missing) picks the transport.

Each provider implements the same :class:`EmailProvider` Protocol so
the outreach endpoint stays provider-agnostic — see ``base.py``.
"""

from __future__ import annotations

from app.services.email_providers.base import (
    EmailAccountNotLinked,
    EmailProvider,
    EmailProviderConfigurationError,
    EmailScopeRequired,
    EmailSendError,
)
from app.services.email_providers.google import GoogleEmailProvider
from app.services.email_providers.microsoft import MicrosoftEmailProvider
from app.services.email_providers.yahoo import YahooEmailProvider


PROVIDERS: dict[str, EmailProvider] = {
    "google": GoogleEmailProvider(),
    "microsoft": MicrosoftEmailProvider(),
    "yahoo": YahooEmailProvider(),
}


__all__ = [
    "EmailAccountNotLinked",
    "EmailProvider",
    "EmailProviderConfigurationError",
    "EmailScopeRequired",
    "EmailSendError",
    "GoogleEmailProvider",
    "MicrosoftEmailProvider",
    "PROVIDERS",
    "YahooEmailProvider",
]
