"""Send a single email through the logged-in user's Gmail account.

Used by the per-contact "Send via Gmail" action in the Outreach modal
on ``/master-list/{id}``. Builds an RFC 822 message, base64url-encodes
it, and POSTs to the Gmail API's ``users.messages.send`` endpoint using
an OAuth bearer token (refreshed by ``services.google_oauth``).

The message lands in the user's own Sent folder and is delivered with
the user's address in the ``From`` header — important because the goal
of this feature is to make outreach come from the BD rep, not from
``noreply@alchemydev.io``.
"""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_SEND_TIMEOUT = 30.0


class GmailScopeRequired(RuntimeError):
    """Gmail API rejected the call due to missing ``gmail.send`` scope.

    Maps to ``412 gmail_scope_required`` in the endpoint, which the
    frontend handles by calling ``authClient.linkSocial`` to add the
    scope and then auto-retrying the send.
    """


class GmailSendError(RuntimeError):
    """Gmail API returned a non-success status outside the scope case."""


async def send_gmail(
    *,
    access_token: str,
    sender_email: str,
    to_email: str,
    subject: str,
    body: str,
) -> str:
    """Send a plaintext email through Gmail; return the message id.

    The message body is sent as ``text/plain`` only — the Gemini-generated
    drafts are plain text, and rich HTML would invite spam-filter
    complications without buying anything for this feature.
    """
    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    payload = {"raw": raw}
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=_SEND_TIMEOUT) as client:
        response = await client.post(_SEND_URL, headers=headers, json=payload)

    if response.status_code == 403:
        body_json = _safe_json(response)
        reason = _first_error_reason(body_json)
        if reason in {"insufficientPermissions", "insufficientScopes"} or (
            "insufficient" in (body_json.get("error", {}).get("message", "") or "").lower()
        ):
            raise GmailScopeRequired(
                "Gmail API rejected the send for missing gmail.send scope."
            )

    if response.status_code >= 400:
        body_text = response.text[:500]
        logger.warning(
            "gmail send failed: status=%s body=%s",
            response.status_code,
            body_text,
        )
        raise GmailSendError(
            f"Gmail API returned status {response.status_code}: {body_text}"
        )

    body_json = _safe_json(response)
    message_id = body_json.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise GmailSendError(
            "Gmail API response did not include a message id."
        )
    return message_id


def _safe_json(response: httpx.Response) -> dict[str, object]:
    """``response.json()`` that never raises — used to parse error bodies."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _first_error_reason(body: dict[str, object]) -> str | None:
    """Pull ``error.errors[0].reason`` from a Google error envelope."""
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    errors = error.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    reason = first.get("reason")
    return reason if isinstance(reason, str) else None
