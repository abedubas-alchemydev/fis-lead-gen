"""Inline verification: syntax + MX presence; on-demand: SMTP RCPT TO.

``check_syntax_and_mx`` runs on every discovered email at insert time
(``email-validator``'s ``validate_email(check_deliverability=True)`` —
syntax parse + DNS MX lookup, sync, offloaded via ``anyio.to_thread.run_sync``).

``check_smtp`` is the on-demand SMTP RCPT TO probe via ``py3-validate-email``
(imports as ``validate_email`` — note the package-vs-import name discrepancy).
Wraps the sync probe in ``anyio.to_thread.run_sync`` so the event loop isn't
blocked on network I/O. Maps the library's three-state return (True/False/None)
onto our ``SmtpStatus`` enum and exceptions onto ``blocked``. Never raises.
"""

from __future__ import annotations

from anyio import to_thread
from email_validator import (
    EmailNotValidError,
    EmailSyntaxError,
    EmailUndeliverableError,
    validate_email,
)
from validate_email import validate_email as _smtp_validate_email  # py3-validate-email

from app.core.config import settings
from app.models.email_verification import SmtpStatus


async def check_syntax_and_mx(email: str) -> tuple[bool, bool, str | None]:
    """Return ``(syntax_valid, mx_present, error_message_or_None)``.

    Never raises. Categorises ``EmailSyntaxError`` as syntax failure (no MX
    lookup attempted), and ``EmailUndeliverableError`` as syntax-OK but MX
    missing. Anything else is treated as syntax failure with the error string.
    """

    def _validate() -> None:
        validate_email(email, check_deliverability=True)

    try:
        await to_thread.run_sync(_validate)
        return True, True, None
    except EmailSyntaxError as exc:
        return False, False, str(exc)
    except EmailUndeliverableError as exc:
        return True, False, str(exc)
    except EmailNotValidError as exc:
        # Catch-all for the parent type — defensive.
        return False, False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, False, f"verification failed: {exc}"


async def check_smtp(email: str) -> tuple[SmtpStatus, str | None]:
    """Perform an SMTP RCPT TO probe against ``email``.

    Never raises. Maps ``py3-validate-email``'s three-state return onto our
    ``SmtpStatus`` enum:
      - ``True``  → ``deliverable``
      - ``False`` → ``undeliverable``
      - ``None``  → ``inconclusive`` (usually a greylist / tempfail)
    Exceptions (SMTP disconnect, DNS failure) are mapped to ``blocked`` with
    the exception class name in ``smtp_message``. Error strings are bare.
    """

    def _probe() -> bool | None:
        return _smtp_validate_email(
            email_address=email,
            check_format=False,  # already validated by check_syntax_and_mx
            check_blacklist=False,  # out of scope for this tool
            check_dns=True,
            dns_timeout=settings.smtp_verify_timeout_seconds,
            check_smtp=True,
            smtp_timeout=settings.smtp_verify_timeout_seconds,
            smtp_helo_host=settings.smtp_verify_helo_host,
            smtp_from_address=settings.smtp_verify_from_address,
            smtp_skip_tls=False,
            smtp_debug=False,
        )

    try:
        result = await to_thread.run_sync(_probe)
    except Exception as exc:  # noqa: BLE001 — py3-validate-email raises a menagerie
        return SmtpStatus.blocked, f"{exc.__class__.__name__}"

    if result is True:
        return SmtpStatus.deliverable, None
    if result is False:
        return SmtpStatus.undeliverable, None
    return SmtpStatus.inconclusive, None
