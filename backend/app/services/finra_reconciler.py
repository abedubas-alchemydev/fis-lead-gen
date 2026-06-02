"""Reconcile a broker-dealer's clearing partner against FINRA Form BD.

Why this exists. Our ``current_clearing_partner`` is extracted from the
firm's latest X-17A-5 FOCUS report (``services/pipeline.py`` →
``services/llm_parser.py``). A 2026-05-28 audit found ~25% of firms had a
clearing partner that disagreed with FINRA BrokerCheck Form BD Item 12
("Introducing Arrangements"). Two causes: (a) the FOCUS audit covers the
*prior* fiscal year, so a firm that changed clearing brokers after the
audited period shows the stale partner (e.g. ORTEX — Apex in our DB,
Alpaca per FINRA, switched 2026-03-19); (b) a now-fixed prompt bug that
mislabeled (k)(2)(ii) introducing brokers as self-clearing.

FINRA Form BD Item 12 is the authoritative *current-state* record of who an
introducing broker clears through. This reconciler treats FOCUS as the
base layer and lets FINRA win on partner identity:

  FOCUS extraction (runs first, owns financials + filing year)
    → FINRA reconcile (this module, owns the current partner identity)

Ordering matters: this must run AFTER the FOCUS clearing extraction has
committed, never as a parallel sibling (see the roadmap — the per-firm
orchestrator wiring lands in a follow-up PR and sequences it post-gather).

What it does per BD:
1. Fetches + parses the BrokerCheck Form BD PDF (reusing
   ``brokercheck_pdf.fetch_form_bd_detail`` — no extra network fetch).
2. Replaces the BD's ``introducing_arrangements`` rows with the parsed
   FINRA entries (additive, FINRA-sourced current-state table).
3. When FINRA names a partner that doesn't match the BD's current partner,
   updates the latest ``clearing_arrangements`` row in place (partner,
   type=fully_disclosed, competitor flag, verified, provenance note) and
   re-derives ``broker_dealers.current_clearing_*`` from it.

The core is idempotent — re-running on an already-reconciled BD is a no-op
(``match``). Driven both by a one-off backfill script and (later) the
per-firm refresh-all orchestrator.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broker_dealer import BrokerDealer
from app.models.clearing_arrangement import ClearingArrangement
from app.models.competitor_provider import CompetitorProvider
from app.models.introducing_arrangement import IntroducingArrangement
from app.services.brokercheck_pdf import (
    FinraPdfFetchError,
    IntroducingArrangementRecord,
    fetch_form_bd_detail,
)
from app.services.broker_dealers import BrokerDealerRepository
from app.services.competitors import CompetitorProviderService

logger = logging.getLogger(__name__)


# Status values returned in ``ReconcileResult.status``.
STATUS_NO_CRD = "no_crd"
STATUS_PDF_MISSING = "pdf_missing"
STATUS_ERROR = "error"
STATUS_FINRA_EMPTY = "finra_empty"
STATUS_MATCH = "match"
STATUS_RECONCILED = "reconciled"
STATUS_BD_NOT_FOUND = "bd_not_found"


@dataclass(frozen=True)
class ReconcileResult:
    bd_id: int
    status: str
    finra_partners: tuple[str, ...] = ()
    previous_partner: Optional[str] = None
    new_partner: Optional[str] = None
    introducing_rows_written: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ReconcileDecision:
    """Pure decision: given our current partner and FINRA's introducing
    partner names (primary first), what should happen?"""

    action: str  # STATUS_FINRA_EMPTY | STATUS_MATCH | STATUS_RECONCILED
    primary_partner: Optional[str]


def _normalize_name(value: Optional[str]) -> str:
    """Strip to lowercase alphanumerics — mirrors
    ``BrokerDealerRepository.normalize_partner_name`` so "Pershing, LLC" and
    "Pershing LLC" collapse to the same key."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def names_match(a: Optional[str], b: Optional[str]) -> bool:
    """True iff two partner strings refer to the same firm.

    Normalized equality, plus containment in either direction with a
    length guard (so "RBC Capital Markets LLC" matches "RBC Capital
    Markets, LLC" but a 3-char fragment can't spuriously match). The guard
    intentionally keeps distinct sibling brands apart — "RBC Clearing &
    Custody" does NOT contain "RBC Capital Markets", so they don't match,
    which is correct (they're different legal entities).
    """
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 6 and shorter in longer


def decide_reconciliation(
    our_partner: Optional[str], finra_partners: list[str]
) -> ReconcileDecision:
    """Pure reconciliation decision (DB-free, unit-tested like
    ``decide_pipelines``).

    ``finra_partners`` is the list of FINRA introducing-arrangement business
    names, primary first (caller orders by effective_date desc). Category
    sentinels in ``our_partner`` ("Self-Clearing" / "Multiple Partners") never
    fuzzy-match a real firm name, so they correctly fall through to reconcile.
    """
    names = [p for p in finra_partners if p and p.strip()]
    if not names:
        return ReconcileDecision(STATUS_FINRA_EMPTY, None)
    primary = names[0]
    if our_partner and any(names_match(our_partner, n) for n in names):
        return ReconcileDecision(STATUS_MATCH, primary)
    return ReconcileDecision(STATUS_RECONCILED, primary)


def _order_partners(
    arrangements: list[IntroducingArrangementRecord],
) -> list[IntroducingArrangementRecord]:
    """Most-recent introducing arrangement first.

    FINRA's "current" clearing firm is the one with the latest Effective
    Date. Entries without a parsed date sort last (date.min) but keep their
    document order via the stable sort.
    """
    return sorted(
        arrangements,
        key=lambda a: a.effective_date or date.min,
        reverse=True,
    )


class FinraClearingReconciler:
    def __init__(
        self,
        repository: Optional[BrokerDealerRepository] = None,
        competitor_service: Optional[CompetitorProviderService] = None,
    ) -> None:
        self.repository = repository or BrokerDealerRepository()
        self.competitors = competitor_service or CompetitorProviderService()

    async def reconcile_for_broker_dealer(
        self,
        db: AsyncSession,
        bd_id: int,
        *,
        competitors: Optional[list[CompetitorProvider]] = None,
        mark_verified: bool = True,
    ) -> ReconcileResult:
        """Reconcile one BD against FINRA Form BD. Commits its own writes.

        ``competitors`` may be passed by a batch caller to avoid re-querying
        the registry per firm; if omitted it's seeded + loaded here.
        """
        bd = await db.get(BrokerDealer, bd_id)
        if bd is None:
            return ReconcileResult(bd_id, STATUS_BD_NOT_FOUND, detail="Broker-dealer not found.")
        if not bd.crd_number:
            return ReconcileResult(bd_id, STATUS_NO_CRD, detail="No CRD on file — cannot fetch BrokerCheck.")

        try:
            detail = await fetch_form_bd_detail(bd.crd_number)
        except FinraPdfFetchError as exc:
            logger.warning("FINRA fetch failed for bd %s (CRD %s): %s", bd_id, bd.crd_number, exc)
            return ReconcileResult(bd_id, STATUS_ERROR, detail=f"FINRA fetch failed: {exc}")
        if detail is None:
            return ReconcileResult(bd_id, STATUS_PDF_MISSING, detail="FINRA has no Form BD PDF for this CRD.")

        ordered = _order_partners(detail.introducing_arrangements)

        # (1) Replace the BD's introducing_arrangements rows with FINRA's
        # current set. Full replace (not upsert) so a removed arrangement
        # doesn't linger — the parsed set is the source of truth.
        await db.execute(
            delete(IntroducingArrangement).where(IntroducingArrangement.bd_id == bd_id)
        )
        for rec in ordered:
            db.add(
                IntroducingArrangement(
                    bd_id=bd_id,
                    statement=rec.statement,
                    business_name=rec.business_name,
                    effective_date=rec.effective_date,
                    description=rec.description,
                )
            )
        rows_written = len(ordered)

        partner_names = [rec.business_name for rec in ordered]
        decision = decide_reconciliation(bd.current_clearing_partner, partner_names)

        if decision.action in (STATUS_FINRA_EMPTY, STATUS_MATCH):
            await db.commit()
            detail_msg = (
                "FINRA shows no introducing arrangement."
                if decision.action == STATUS_FINRA_EMPTY
                else f"FINRA agrees: {bd.current_clearing_partner}."
            )
            return ReconcileResult(
                bd_id,
                decision.action,
                finra_partners=tuple(partner_names),
                previous_partner=bd.current_clearing_partner,
                new_partner=bd.current_clearing_partner,
                introducing_rows_written=rows_written,
                detail=detail_msg,
            )

        # (2) Reconcile: FINRA names a partner that disagrees with ours.
        if competitors is None:
            await self.competitors.seed_defaults(db)
            competitors = await self.competitors.list_active(db)

        primary = ordered[0]
        primary_name = primary.business_name
        previous_partner = bd.current_clearing_partner

        await self._apply_partner(
            db,
            bd=bd,
            partner=primary,
            competitors=competitors,
            previous_partner=previous_partner,
            mark_verified=mark_verified,
        )
        await db.commit()

        return ReconcileResult(
            bd_id,
            STATUS_RECONCILED,
            finra_partners=tuple(partner_names),
            previous_partner=previous_partner,
            new_partner=primary_name,
            introducing_rows_written=rows_written,
            detail=f"Reconciled {previous_partner or 'Self-Clearing'} -> {primary_name} (FINRA effective {primary.effective_date or 'unknown'}).",
        )

    async def _apply_partner(
        self,
        db: AsyncSession,
        *,
        bd: BrokerDealer,
        partner: IntroducingArrangementRecord,
        competitors: list[CompetitorProvider],
        previous_partner: Optional[str],
        mark_verified: bool,
    ) -> None:
        """Write the FINRA partner onto the latest clearing_arrangements row
        (creating one if none exists) and re-derive the BD rollup. Mirrors
        ``pipeline.py::_refresh_clearing_rollup_for_bd``.
        """
        partner_name = partner.business_name
        is_competitor = self.repository.match_competitor(partner_name, competitors)
        normalized = self.repository.normalize_partner_name(partner_name)
        provenance = (
            f"[finra_reconciled {datetime.now(timezone.utc).date().isoformat()}] "
            f"Partner set from FINRA Form BD Item 12"
            + (f" (was: {previous_partner})." if previous_partner else " (was: Self-Clearing/none).")
        )

        latest = (
            await db.execute(
                select(ClearingArrangement)
                .where(ClearingArrangement.bd_id == bd.id)
                .order_by(ClearingArrangement.filing_year.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if latest is None:
            # No FOCUS row to anchor on — create a FINRA-sourced row. Use the
            # arrangement's effective year (falls back to current year) as
            # filing_year to satisfy the (bd_id, filing_year) unique key.
            filing_year = (
                partner.effective_date.year
                if partner.effective_date
                else datetime.now(timezone.utc).year
            )
            latest = ClearingArrangement(
                bd_id=bd.id,
                filing_year=filing_year,
                report_date=partner.effective_date,
                extraction_status="parsed",
                extraction_confidence=None,
            )
            db.add(latest)

        latest.clearing_partner = partner_name
        latest.normalized_partner = normalized
        latest.clearing_type = "fully_disclosed"
        latest.is_competitor = is_competitor
        latest.is_verified = mark_verified
        latest.extraction_notes = (
            f"{provenance} {latest.extraction_notes}".strip()
            if latest.extraction_notes
            else provenance
        )
        await db.flush()

        # Re-derive the BD rollup from the row we just wrote.
        bd.current_clearing_partner = partner_name
        bd.current_clearing_type = "fully_disclosed"
        # Unify: the reconciler is authoritative for introducing firms, so keep
        # the clearing_classification column in lock-step with the rollup type.
        bd.clearing_classification = "fully_disclosed"
        bd.current_clearing_is_competitor = is_competitor
        if latest.report_date is not None:
            bd.last_audit_report_date = latest.report_date
        await db.flush()


__all__ = [
    "FinraClearingReconciler",
    "ReconcileDecision",
    "ReconcileResult",
    "decide_reconciliation",
    "names_match",
    "STATUS_BD_NOT_FOUND",
    "STATUS_ERROR",
    "STATUS_FINRA_EMPTY",
    "STATUS_MATCH",
    "STATUS_NO_CRD",
    "STATUS_PDF_MISSING",
    "STATUS_RECONCILED",
]
