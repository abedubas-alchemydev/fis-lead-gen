"""Real provider fan-out.

Replaces the prior 1.5s sleep stub. Sequence per scan:

    1. Mark run ``running``, set ``started_at``.
    2. ``asyncio.gather`` all enabled providers (return_exceptions=True so
       one provider raising doesn't kill the run).
    3. Dedupe drafts across providers on lowercased email; keep highest
       confidence.
    4. Insert one ``DiscoveredEmail`` row per deduped draft, plus an inline
       ``EmailVerification`` row from ``check_syntax_and_mx``.
    5. Update counters + ``error_message`` from any provider failures.
    6. Mark ``completed`` (or ``failed`` if every provider raised), set
       ``completed_at``.

Providers are dependency-injected via the ``providers`` parameter so tests
can pass fakes. In production, the endpoint passes nothing and we use the
default list.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import anyio

from app.db.session import SessionLocal
from app.models.discovered_email import DiscoveredEmail
from app.models.email_verification import EmailVerification, SmtpStatus
from app.models.extraction_run import ExtractionRun, RunStatus
from app.services.email_extractor.base import DiscoveredEmailDraft, EmailSource
from app.services.email_extractor.hunter import Hunter
from app.services.email_extractor.site_crawler import SiteCrawler
from app.services.email_extractor.snov import Snov
from app.services.email_extractor.theharvester import TheHarvester
from app.services.email_extractor.verification import check_syntax_and_mx

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_CHAR_CAP = 4000


def default_providers() -> list[EmailSource]:
    return [SiteCrawler(), Hunter(), TheHarvester(), Snov()]


async def run(run_id: int, providers: list[EmailSource] | None = None) -> None:
    if providers is None:
        providers = default_providers()

    domain = await _begin_run(run_id)
    if domain is None:
        return

    try:
        deduped, errors, failed_providers = await _fan_out(providers, domain)

        success, failure, persist_errors = await _persist_drafts(run_id, domain, deduped)
        errors.extend(persist_errors)

        final_status = _final_status(providers, failed_providers)

        await _finalize_run(
            run_id=run_id,
            status=final_status,
            total=len(deduped),
            success=success,
            failure=failure,
            errors=errors,
        )
    except Exception as exc:
        # Crash-safety: any uncaught error in fan-out / persist / finalize would
        # otherwise leave the run stuck at status="running" forever (a crashed
        # FastAPI BackgroundTask doesn't rollback the started-state). Write a
        # terminal `failed` row so the frontend stops polling, then re-raise so
        # the traceback still reaches logs/observability.
        logger.exception("aggregator.run crashed for run_id=%s", run_id)
        await _finalize_run(
            run_id=run_id,
            status=RunStatus.failed.value,
            total=0,
            success=0,
            failure=0,
            errors=[f"aggregator crash: {type(exc).__name__}: {exc}"],
        )
        raise


async def _begin_run(run_id: int) -> str | None:
    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        if scan is None:
            logger.warning("aggregator.run: run_id=%s not found", run_id)
            return None
        scan.status = RunStatus.running.value
        scan.started_at = datetime.now(UTC)
        domain = scan.domain
        await session.commit()
        return domain


def _sortable_confidence(c: float | None) -> float:
    """Sentinel for ordering drafts: `None` = no information = always loses.

    theHarvester deterministically emits `confidence=None`; Hunter and Snov
    do so conditionally when upstream APIs omit the field. Comparing `None`
    directly to a float raises `TypeError`, which crashed a production scan
    on 2026-04-20 (www.southloop.vc); this helper makes the dedup loop
    tolerant. See reports/aggregator-dedup-audit-2026-04-20.md Follow-up #2.
    """
    return c if c is not None else float("-inf")


async def _fan_out(providers: list[EmailSource], domain: str) -> tuple[dict[str, DiscoveredEmailDraft], list[str], int]:
    deduped: dict[str, DiscoveredEmailDraft] = {}
    errors: list[str] = []
    failed_providers = 0

    outcomes: list[tuple[EmailSource, object]] = []

    async def _safe_run(provider: EmailSource) -> tuple[EmailSource, object]:
        try:
            return provider, await provider.run(domain)
        except Exception as exc:  # noqa: BLE001
            return provider, exc

    async def _collect(provider: EmailSource) -> None:
        outcomes.append(await _safe_run(provider))

    async with anyio.create_task_group() as tg:
        for provider in providers:
            tg.start_soon(_collect, provider)

    for provider, outcome in outcomes:
        if isinstance(outcome, Exception):
            failed_providers += 1
            errors.append(f"{provider.name}: {type(outcome).__name__}: {outcome}")
            continue
        # outcome is DiscoveryResult — duck-typed access to keep the import surface small.
        emails = getattr(outcome, "emails", []) or []
        provider_errors = getattr(outcome, "errors", []) or []
        for err in provider_errors:
            errors.append(f"{provider.name}: {err}")
        for draft in emails:
            key = draft.email.lower()
            existing = deduped.get(key)
            if existing is None or _sortable_confidence(draft.confidence) > _sortable_confidence(existing.confidence):
                deduped[key] = draft

    return deduped, errors, failed_providers


async def _persist_drafts(
    run_id: int, domain: str, deduped: dict[str, DiscoveredEmailDraft]
) -> tuple[int, int, list[str]]:
    success = 0
    failure = 0
    errors: list[str] = []

    for draft in deduped.values():
        async with SessionLocal() as session:
            try:
                de = DiscoveredEmail(
                    run_id=run_id,
                    email=draft.email,
                    domain=domain,
                    source=draft.source,
                    confidence=draft.confidence,
                    attribution=draft.attribution,
                )
                session.add(de)
                await session.flush()

                syntax_ok, mx_ok, err = await check_syntax_and_mx(draft.email)
                ver = EmailVerification(
                    discovered_email_id=de.id,
                    syntax_valid=syntax_ok,
                    mx_record_present=mx_ok,
                    smtp_status=SmtpStatus.not_checked.value,
                    smtp_message=err,
                )
                session.add(ver)
                await session.commit()
                success += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                failure += 1
                errors.append(f"persist {draft.email}: {exc}")

    return success, failure, errors


def _final_status(providers: list[EmailSource], failed_providers: int) -> str:
    if providers and failed_providers == len(providers):
        return RunStatus.failed.value
    return RunStatus.completed.value


async def _finalize_run(
    *,
    run_id: int,
    status: str,
    total: int,
    success: int,
    failure: int,
    errors: list[str],
) -> None:
    async with SessionLocal() as session:
        scan = await session.get(ExtractionRun, run_id)
        if scan is None:
            return
        scan.status = status
        scan.total_items = total
        scan.processed_items = success + failure
        scan.success_count = success
        scan.failure_count = failure
        if errors:
            joined = "\n".join(errors)
            scan.error_message = joined[:_ERROR_MESSAGE_CHAR_CAP]
        scan.completed_at = datetime.now(UTC)
        await session.commit()
