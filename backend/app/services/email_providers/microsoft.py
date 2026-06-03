"""Microsoft (Outlook) email provider via the Graph ``users.sendMail`` API.

Sends through the logged-in user's mailbox using a refreshed OAuth
bearer token. Mail.Send is the only Graph scope required; we don't
read the user's mailbox or list messages, just emit one outbound
message per call.

Graph returns 202 Accepted with no message id on success, so the
provider returns a synthetic ``microsoft-{iso8601}`` placeholder that
lands in ``outreach_sends.gmail_message_id``. The audit row's
``provider`` column ("microsoft") plus this placeholder is enough for
admin lookup; the column name is legacy from the Gmail-only era and
will be renamed in a cleanup PR.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.email_providers.base import (
    EmailScopeRequired,
    EmailSendError,
)
from app.services.email_providers.microsoft_oauth import (
    get_fresh_microsoft_access_token,
)


logger = logging.getLogger(__name__)


# Per https://learn.microsoft.com/en-us/graph/api/user-sendmail
SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
SEND_SCOPE = "Mail.Send"
_SEND_TIMEOUT = 30.0


class MicrosoftEmailProvider:
    provider_id: str = "microsoft"
    send_scope: str = SEND_SCOPE

    async def get_fresh_token(
        self, db: AsyncSession, account_id: str
    ) -> tuple[str, list[str]]:
        return await get_fresh_microsoft_access_token(
            db=db, account_id=account_id
        )

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
        """Send via Graph and return a synthetic message id.

        Graph's sendMail wraps the message in JSON (not RFC 822 raw like
        Gmail). ``ccRecipients`` are visible; ``bccRecipients`` are
        hidden from other recipients by Graph natively.
        saveToSentItems=true so the message appears in the user's Sent
        Items folder — parity with Gmail's default behavior.
        """

        message: dict[str, object] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in to_emails
            ],
        }
        if cc_emails:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc_emails
            ]
        if bcc_emails:
            message["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc_emails
            ]
        payload = {
            "message": message,
            "saveToSentItems": True,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
            response = await client.post(
                SEND_MAIL_URL, headers=headers, json=payload
            )

        if response.status_code in (401, 403):
            body_json = _safe_json(response)
            error_code = _graph_error_code(body_json)
            if error_code in {
                "InvalidAuthenticationToken",
                "AuthenticationFailed",
                "TokenNotFound",
                "MissingScope",
                "AccessDenied",
            }:
                # Treat token / scope failures as "needs re-consent" so
                # the endpoint maps to 412 and the FE prompts linkSocial.
                raise EmailScopeRequired(
                    f"Microsoft Graph rejected the send for missing scope "
                    f"or invalid token: {error_code or 'unspecified'}."
                )

        if response.status_code >= 400:
            body_text = response.text[:500]
            logger.warning(
                "microsoft graph send failed: status=%s body=%s",
                response.status_code,
                body_text,
            )
            raise EmailSendError(
                f"Microsoft Graph returned status {response.status_code}: "
                f"{body_text}"
            )

        # Graph returns 202 with no body. Synthesize a placeholder id so
        # the audit row has something searchable.
        return _synthetic_message_id()


def _synthetic_message_id() -> str:
    return f"microsoft-{datetime.now(timezone.utc).isoformat()}"


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _graph_error_code(body: dict[str, object]) -> str | None:
    """Pull ``error.code`` from a Graph error envelope."""

    error = body.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None
