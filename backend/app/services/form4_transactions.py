"""Repository for ``form4_transactions``.

Read + write surface for the Investors tab. Watcher upserts via
``upsert_many``; the FE list endpoint reads via ``list_transactions``;
the per-row Enrich button writes via ``attach_enrichment``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import ceil
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.form4_transaction import Form4Transaction
from app.services.service_models import Form4TransactionRecord


class Form4TransactionRepository:
    async def upsert_many(
        self,
        db: AsyncSession,
        records: list[Form4TransactionRecord],
    ) -> int:
        """Bulk-insert Form 4 transactions, ignoring conflicts on ``dedupe_key``.

        Returns the number of rows actually inserted (excludes duplicates from
        the EFTS overlap window). Unlike ``AlertRepository.upsert_many`` we
        ``DO NOTHING`` on conflict instead of ``DO UPDATE`` — Form 4 facts are
        immutable once filed (you can't retroactively change a past insider
        transaction), so refreshing the columns serves no purpose.
        """
        if not records:
            return 0

        stmt = insert(Form4Transaction).values(
            [
                {
                    "accession_number": r.accession_number,
                    "transaction_index": r.transaction_index,
                    "is_derivative": r.is_derivative,
                    "dedupe_key": r.dedupe_key,
                    "issuer_cik": r.issuer_cik,
                    "issuer_name": r.issuer_name,
                    "issuer_ticker": r.issuer_ticker,
                    "reporting_owner_cik": r.reporting_owner_cik,
                    "reporting_owner_name": r.reporting_owner_name,
                    "reporting_owner_is_director": r.reporting_owner_is_director,
                    "reporting_owner_is_officer": r.reporting_owner_is_officer,
                    "reporting_owner_is_ten_pct": r.reporting_owner_is_ten_pct,
                    "reporting_owner_title": r.reporting_owner_title,
                    "reporting_owner_street1": r.reporting_owner_street1,
                    "reporting_owner_street2": r.reporting_owner_street2,
                    "reporting_owner_city": r.reporting_owner_city,
                    "reporting_owner_state": r.reporting_owner_state,
                    "reporting_owner_zip": r.reporting_owner_zip,
                    "security_title": r.security_title,
                    "transaction_date": r.transaction_date,
                    "transaction_code": r.transaction_code,
                    "ad_code": r.ad_code,
                    "shares": r.shares,
                    "price_per_share": r.price_per_share,
                    "transaction_value": r.transaction_value,
                    "source_filing_url": r.source_filing_url,
                    "filed_at": r.filed_at,
                }
                for r in records
            ]
        )
        upsert_stmt = stmt.on_conflict_do_nothing(
            index_elements=[Form4Transaction.dedupe_key]
        ).returning(Form4Transaction.id)
        result = await db.execute(upsert_stmt)
        return len(result.all())

    async def list_transactions(
        self,
        db: AsyncSession,
        *,
        ad_code: Literal["A", "D"] | None,
        ticker: str | None,
        days: int,
        min_value: Decimal | None,
        page: int,
        limit: int,
    ) -> tuple[list[Form4Transaction], int]:
        """Paginated read.

        Ordered by (ticker ASC, transaction_date DESC, id DESC) so the
        FE's "grouped by company" rendering can stream rows directly
        without re-sorting client-side. Returns (rows, total).
        """
        filters = []
        if ad_code is not None:
            filters.append(Form4Transaction.ad_code == ad_code)
        if ticker:
            filters.append(
                func.upper(Form4Transaction.issuer_ticker) == ticker.upper()
            )
        if days > 0:
            cutoff = date.today() - timedelta(days=days)
            filters.append(Form4Transaction.transaction_date >= cutoff)
        if min_value is not None:
            filters.append(Form4Transaction.transaction_value >= min_value)

        count_stmt = select(func.count(Form4Transaction.id))
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int((await db.execute(count_stmt)).scalar_one())

        stmt = (
            select(Form4Transaction)
            .order_by(
                Form4Transaction.issuer_ticker.asc().nullslast(),
                Form4Transaction.transaction_date.desc(),
                Form4Transaction.id.desc(),
            )
            .offset((page - 1) * limit)
            .limit(limit)
        )
        if filters:
            stmt = stmt.where(*filters)

        rows = (await db.execute(stmt)).scalars().all()
        return list(rows), total

    async def get(self, db: AsyncSession, txn_id: int) -> Form4Transaction | None:
        return await db.get(Form4Transaction, txn_id)

    async def attach_enrichment(
        self,
        db: AsyncSession,
        txn_id: int,
        *,
        phone: str | None,
        email: str | None,
    ) -> Form4Transaction | None:
        """Apply per-row Apollo enrichment.

        Returns the refreshed row or ``None`` if the id doesn't exist.
        ``enriched_at`` is always set so future "re-enrich after N days"
        logic has a timestamp to compare against, even when both phone
        and email come back empty.
        """
        row = await self.get(db, txn_id)
        if row is None:
            return None
        row.enriched_phone = phone
        row.enriched_email = email
        row.enriched_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        return row


def total_pages(total: int, limit: int) -> int:
    if limit <= 0:
        return 1
    return max(1, ceil(total / limit))
