"""Background-task orchestration for SMTP verification batches.

POST /verify schedules `run_smtp_verification` via FastAPI `BackgroundTasks`.
The task opens its own `AsyncSession` because the request-scoped session is
closed by the time the response has been sent.

Concurrency model (carried from PR #13):
- `asyncio.Semaphore(settings.smtp_verify_concurrency)` caps simultaneous
  SMTP probes (default 1 — a per-domain cap is a future prompt).
- `asyncio.Lock()` serializes the DB-write window because `AsyncSession`
  isn't safe for concurrent add/flush/refresh across coroutines. Writes
  are microseconds vs probe-seconds, so contention is negligible.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.discovered_email import DiscoveredEmail
from app.models.email_verification import EmailVerification, SmtpStatus
from app.models.extraction_run import RunStatus
from app.models.verification_run import VerificationRun
from app.services.email_extractor.verification import check_smtp


async def run_smtp_verification(verify_run_id: int, email_ids: list[int]) -> None:
    """Process one VerificationRun: fan out SMTP probes, persist results, update counters.

    On uncaught exception: sets status=failed + populates error_message, then re-raises.
    """
    async with SessionLocal() as db:
        run = await db.get(VerificationRun, verify_run_id)
        if run is None:
            return
        run.status = RunStatus.running.value
        await db.commit()

        try:
            rows = (await db.execute(select(DiscoveredEmail).where(DiscoveredEmail.id.in_(email_ids)))).scalars().all()
            by_id = {row.id: row for row in rows}

            semaphore = asyncio.Semaphore(settings.smtp_verify_concurrency)
            db_lock = asyncio.Lock()

            async def _verify_one(discovered: DiscoveredEmail) -> str:
                async with semaphore:
                    smtp_status, smtp_message = await check_smtp(discovered.email)
                async with db_lock:
                    verification = EmailVerification(
                        discovered_email_id=discovered.id,
                        smtp_status=smtp_status.value,
                        smtp_message=smtp_message,
                    )
                    db.add(verification)
                    await db.flush()
                    await db.refresh(verification)
                    return verification.smtp_status

            async def _resolve(email_id: int) -> str:
                discovered = by_id.get(email_id)
                if discovered is None:
                    return SmtpStatus.not_checked.value
                return await _verify_one(discovered)

            statuses = await asyncio.gather(*(_resolve(eid) for eid in email_ids))

            run.processed_items = len(statuses)
            run.success_count = sum(
                1 for s in statuses if s in {SmtpStatus.deliverable.value, SmtpStatus.inconclusive.value}
            )
            run.failure_count = sum(
                1 for s in statuses if s in {SmtpStatus.undeliverable.value, SmtpStatus.blocked.value}
            )
            run.status = RunStatus.completed.value
            run.completed_at = datetime.now(tz=UTC)
            await db.commit()
        except Exception as exc:
            run.status = RunStatus.failed.value
            run.error_message = str(exc)[:500]
            run.completed_at = datetime.now(tz=UTC)
            await db.commit()
            raise
