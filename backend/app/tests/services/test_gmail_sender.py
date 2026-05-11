"""Unit tests for gmail_sender.send_gmail.

All HTTP is mocked via respx. These tests pin the three failure modes
the send endpoint maps to user-facing 412/502 responses:

  - 403 ``insufficientPermissions`` → ``GmailScopeRequired`` (412 on
    the wire). The FE handles this by prompting Google's incremental
    consent for the ``gmail.send`` scope.
  - Other 4xx/5xx → ``GmailSendError`` (502). The FE shows a generic
    "Gmail rejected the message" message and lets the user retry.
  - 2xx with no ``id`` → ``GmailSendError``. Unlikely from real Gmail
    but cheap to guard.
"""

from __future__ import annotations

import base64
import re

import httpx
import pytest
import respx

from app.services.gmail_sender import (
    GmailScopeRequired,
    GmailSendError,
    send_gmail,
)


_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


@respx.mock
async def test_send_gmail_returns_message_id_on_success() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"id": "msg-abc-123"})

    respx.post(_SEND_URL).mock(side_effect=_capture)

    message_id = await send_gmail(
        access_token="tok-xyz",
        sender_email="rep@alchemydev.io",
        to_email="contact@firm.example",
        subject="Re: clearing services",
        body="Hi Jane,\n\nWanted to introduce our new custody offering.\n\nArvin",
    )

    assert message_id == "msg-abc-123"
    assert captured["headers"].get("authorization") == "Bearer tok-xyz"
    # Body is wrapped as ``{"raw": <base64url RFC822>}`` so confirm the
    # encoding actually round-trips through Gmail's expected shape.
    body_str = captured["json"]
    raw_match = re.search(r'"raw":\s*"([^"]+)"', body_str)
    assert raw_match is not None
    raw = raw_match.group(1)
    # Add padding back for decoding (Gmail strips it per RFC 4648 §5).
    padded = raw + "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    assert "From: rep@alchemydev.io" in decoded
    assert "To: contact@firm.example" in decoded
    assert "Subject: Re: clearing services" in decoded
    assert "introduce our new custody offering" in decoded


@respx.mock
async def test_send_gmail_raises_scope_required_on_insufficient_permissions() -> None:
    respx.post(_SEND_URL).mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "Request had insufficient authentication scopes.",
                    "errors": [{"reason": "insufficientPermissions"}],
                }
            },
        )
    )

    with pytest.raises(GmailScopeRequired):
        await send_gmail(
            access_token="tok-xyz",
            sender_email="rep@alchemydev.io",
            to_email="contact@firm.example",
            subject="s",
            body="b",
        )


@respx.mock
async def test_send_gmail_raises_send_error_on_5xx() -> None:
    respx.post(_SEND_URL).mock(
        return_value=httpx.Response(503, json={"error": {"message": "backend"}})
    )

    with pytest.raises(GmailSendError):
        await send_gmail(
            access_token="tok-xyz",
            sender_email="rep@alchemydev.io",
            to_email="contact@firm.example",
            subject="s",
            body="b",
        )


@respx.mock
async def test_send_gmail_raises_send_error_when_response_missing_id() -> None:
    respx.post(_SEND_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GmailSendError):
        await send_gmail(
            access_token="tok-xyz",
            sender_email="rep@alchemydev.io",
            to_email="contact@firm.example",
            subject="s",
            body="b",
        )
