"""Yahoo Mail email provider via SMTP + XOAUTH2.

Yahoo doesn't ship a REST sendmail API. SMTP over ``smtp.mail.yahoo.com``
with the XOAUTH2 SASL mechanism is the canonical OAuth send path.

We use :mod:`aiosmtplib` (a non-blocking SMTP client built on asyncio)
because the outreach send endpoint is async and the synchronous
``smtplib`` would block the event loop for the duration of the
round-trip (handshake + DATA up to ~30s on a busy MTA).

aiosmtplib's ``login()`` only ships LOGIN / PLAIN / CRAM-MD5 auth, so
the XOAUTH2 SASL string is built and emitted via ``execute_command``
directly. This is the same approach the official Google + Yahoo
sample code recommends.

Returns a synthetic ``yahoo-{iso8601}`` placeholder for the audit row
since SMTP doesn't yield a server-side message id we can rely on.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.email_providers.base import (
    EmailScopeRequired,
    EmailSendError,
)
from app.services.email_providers.yahoo_oauth import get_fresh_yahoo_access_token


logger = logging.getLogger(__name__)


# Per https://developer.yahoo.com/api/mail/
SMTP_HOST = "smtp.mail.yahoo.com"
SMTP_PORT = 587
SEND_SCOPE = "mail-w"
_SEND_TIMEOUT = 30.0


class YahooEmailProvider:
    provider_id: str = "yahoo"
    send_scope: str = SEND_SCOPE

    async def get_fresh_token(
        self, db: AsyncSession, user_id: str
    ) -> tuple[str, list[str]]:
        return await get_fresh_yahoo_access_token(db=db, user_id=user_id)

    async def send(
        self,
        *,
        access_token: str,
        sender_email: str,
        to_email: str,
        subject: str,
        body: str,
    ) -> str:
        """Send via SMTP STARTTLS + XOAUTH2; return synthetic id."""

        message = EmailMessage()
        message["From"] = sender_email
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        # RFC 7628 XOAUTH2 SASL initial-response. The literal \x01 byte
        # is the SASL message separator; base64 of the whole thing is
        # the AUTH command argument.
        auth_string = (
            f"user={sender_email}\x01auth=Bearer {access_token}\x01\x01"
        )
        auth_b64 = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")

        smtp = aiosmtplib.SMTP(
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            start_tls=False,  # explicit STARTTLS upgrade below; Yahoo's
                              # 587 endpoint doesn't accept implicit TLS
            timeout=_SEND_TIMEOUT,
        )
        try:
            await smtp.connect()
            await smtp.starttls()
            try:
                # aiosmtplib doesn't ship an XOAUTH2 helper; emit the
                # AUTH command + base64 arg directly. Code 235 = auth
                # success; anything else means scope / token rejected.
                response = await smtp.execute_command(
                    b"AUTH", b"XOAUTH2", auth_b64.encode("ascii")
                )
                if response.code != 235:
                    raise EmailScopeRequired(
                        f"Yahoo SMTP rejected XOAUTH2 auth: "
                        f"{response.code} {response.message}"
                    )
            except aiosmtplib.SMTPException as exc:
                # SMTP-level failure during AUTH (network blip, server
                # closed connection). Treat as send-side error so the
                # FE doesn't keep prompting re-consent on a transient.
                logger.warning("yahoo SMTP auth failed: %s", exc)
                raise EmailSendError(f"Yahoo SMTP auth failed: {exc}") from exc

            try:
                await smtp.send_message(message)
            except aiosmtplib.SMTPException as exc:
                logger.warning("yahoo SMTP send failed: %s", exc)
                raise EmailSendError(f"Yahoo SMTP send failed: {exc}") from exc
        finally:
            try:
                await smtp.quit()
            except aiosmtplib.SMTPException:
                # Best-effort close; don't shadow a real send-side error
                # with quit-time noise.
                pass

        return _synthetic_message_id()


def _synthetic_message_id() -> str:
    return f"yahoo-{datetime.now(timezone.utc).isoformat()}"
