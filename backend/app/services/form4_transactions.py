"""Repository for ``form4_transactions``.

Read + write surface for the Investors tab. Watcher upserts via
``upsert_many``; the FE list endpoint reads via ``list_consolidated_persons``
(one row per person × issuer, with shares + value summed across the
filter window); the per-row Enrich button writes via
``attach_enrichment_by_person`` so a single Apollo match populates phone /
email on every underlying transaction the same person filed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import ceil
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.form4_transaction import Form4Transaction
from app.services.service_models import (
    ConsolidatedPersonRow,
    Form4TransactionRecord,
)


_FORM4_COLUMN_COUNT = 27
# Postgres caps bound parameters per statement at 65535. With 27 columns
# per row, the hard ceiling is 65535 / 27 ≈ 2427. We chunk at 1500 to keep
# headroom for future column additions without revisiting the math.
_UPSERT_CHUNK_SIZE = 1500


class Form4TransactionRepository:
    async def upsert_many(
        self,
        db: AsyncSession,
        records: list[Form4TransactionRecord],
    ) -> int:
        """Bulk-insert Form 4 transactions, ignoring conflicts on ``dedupe_key``.

        Chunks the insert into ``_UPSERT_CHUNK_SIZE``-row batches to stay
        under Postgres's 65535-bound-parameter ceiling. A 7-day Form 4
        sweep regularly produces 5-10k records, well above what one
        prepared statement can carry.

        Returns the number of rows actually inserted (excludes duplicates from
        the EFTS overlap window). Unlike ``AlertRepository.upsert_many`` we
        ``DO NOTHING`` on conflict instead of ``DO UPDATE`` — Form 4 facts are
        immutable once filed (you can't retroactively change a past insider
        transaction), so refreshing the columns serves no purpose.
        """
        if not records:
            return 0

        total_inserted = 0
        for offset in range(0, len(records), _UPSERT_CHUNK_SIZE):
            chunk = records[offset : offset + _UPSERT_CHUNK_SIZE]
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
                    for r in chunk
                ]
            )
            upsert_stmt = stmt.on_conflict_do_nothing(
                index_elements=[Form4Transaction.dedupe_key]
            ).returning(Form4Transaction.id)
            result = await db.execute(upsert_stmt)
            total_inserted += len(result.all())
        return total_inserted

    async def list_consolidated_persons(
        self,
        db: AsyncSession,
        *,
        ad_code: Literal["A", "D"] | None,
        ticker: str | None,
        days: int,
        min_value: Decimal | None,
        page: int,
        limit: int,
    ) -> tuple[list[ConsolidatedPersonRow], int]:
        """Paginated list of consolidated person rows.

        Collapses every ``form4_transactions`` row matching the filter
        window into one row per ``(issuer_cik, reporting_owner_cik, ad_code)``
        group. Sums shares + transaction_value across the group; pulls
        leader-row metadata (name, title, address, security_title, filing
        pointer, enrichment) from the most recent transaction in the
        group.

        Pagination is over groups, so ``total`` is the number of distinct
        persons in the filter window — not the raw transaction count.
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

        partition = (
            Form4Transaction.issuer_cik,
            Form4Transaction.reporting_owner_cik,
            Form4Transaction.ad_code,
        )

        rn = func.row_number().over(
            partition_by=partition,
            order_by=(
                Form4Transaction.filed_at.desc(),
                Form4Transaction.transaction_date.desc(),
                Form4Transaction.id.desc(),
            ),
        ).label("rn")
        sum_shares = (
            func.sum(Form4Transaction.shares).over(partition_by=partition).label("sum_shares")
        )
        sum_value = (
            func.sum(Form4Transaction.transaction_value)
            .over(partition_by=partition)
            .label("sum_value")
        )
        txn_count = func.count().over(partition_by=partition).label("txn_count")
        max_txn_date = (
            func.max(Form4Transaction.transaction_date)
            .over(partition_by=partition)
            .label("max_txn_date")
        )

        ranked_stmt = select(
            Form4Transaction,
            rn,
            sum_shares,
            sum_value,
            txn_count,
            max_txn_date,
        )
        if filters:
            ranked_stmt = ranked_stmt.where(*filters)
        ranked = ranked_stmt.subquery("ranked")

        # Group count for pagination meta — distinct group keys after filters.
        group_subq_stmt = select(
            Form4Transaction.issuer_cik,
            Form4Transaction.reporting_owner_cik,
            Form4Transaction.ad_code,
        ).group_by(
            Form4Transaction.issuer_cik,
            Form4Transaction.reporting_owner_cik,
            Form4Transaction.ad_code,
        )
        if filters:
            group_subq_stmt = group_subq_stmt.where(*filters)
        count_stmt = select(func.count()).select_from(group_subq_stmt.subquery())
        total = int((await db.execute(count_stmt)).scalar_one())

        page_stmt = (
            select(ranked)
            .where(ranked.c.rn == 1)
            .order_by(
                ranked.c.issuer_ticker.asc().nullslast(),
                ranked.c.max_txn_date.desc(),
                ranked.c.sum_value.desc().nullslast(),
            )
            .offset((page - 1) * limit)
            .limit(limit)
        )
        result = await db.execute(page_stmt)
        rows = [_row_to_consolidated(mapping) for mapping in result.mappings().all()]
        return rows, total

    async def get(self, db: AsyncSession, txn_id: int) -> Form4Transaction | None:
        return await db.get(Form4Transaction, txn_id)

    async def attach_enrichment_by_person(
        self,
        db: AsyncSession,
        *,
        reporting_owner_cik: str,
        phone: str | None,
        email: str | None,
    ) -> tuple[int, datetime]:
        """Apply Apollo enrichment to every ``form4_transactions`` row for a person.

        A single Apollo lookup yields phone/email for one reporting
        person; CIK is the SEC-assigned per-person identifier, so writing
        WHERE ``reporting_owner_cik = X`` covers all of that person's
        transactions across every issuer they appear under. ``enriched_at``
        is always stamped so the FE can distinguish "never enriched" from
        "enriched, came back empty" without re-firing Apollo.

        Returns ``(rowcount, enriched_at)``.
        """
        enriched_at = datetime.now(timezone.utc)
        stmt = (
            update(Form4Transaction)
            .where(Form4Transaction.reporting_owner_cik == reporting_owner_cik)
            .values(
                enriched_phone=phone,
                enriched_email=email,
                enriched_at=enriched_at,
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return (result.rowcount or 0), enriched_at


def total_pages(total: int, limit: int) -> int:
    if limit <= 0:
        return 1
    return max(1, ceil(total / limit))


def _row_to_consolidated(mapping) -> ConsolidatedPersonRow:
    """Build a ``ConsolidatedPersonRow`` from a SQLAlchemy mapping row.

    ``price_per_share`` collapses to None when the group has more than one
    underlying transaction — a single per-share price is meaningless across
    multiple trades at different prices, and the FE relies on this signal
    to hide the "@ price" decoration on consolidated rows.
    """
    txn_count = int(mapping["txn_count"])
    price = mapping["price_per_share"] if txn_count == 1 else None
    return ConsolidatedPersonRow(
        id=mapping["id"],
        accession_number=mapping["accession_number"],
        is_derivative=mapping["is_derivative"],
        issuer_cik=mapping["issuer_cik"],
        issuer_name=mapping["issuer_name"],
        issuer_ticker=mapping["issuer_ticker"],
        reporting_owner_cik=mapping["reporting_owner_cik"],
        reporting_owner_name=mapping["reporting_owner_name"],
        reporting_owner_title=mapping["reporting_owner_title"],
        reporting_owner_is_director=mapping["reporting_owner_is_director"],
        reporting_owner_is_officer=mapping["reporting_owner_is_officer"],
        reporting_owner_is_ten_pct=mapping["reporting_owner_is_ten_pct"],
        reporting_owner_street1=mapping["reporting_owner_street1"],
        reporting_owner_street2=mapping["reporting_owner_street2"],
        reporting_owner_city=mapping["reporting_owner_city"],
        reporting_owner_state=mapping["reporting_owner_state"],
        reporting_owner_zip=mapping["reporting_owner_zip"],
        security_title=mapping["security_title"],
        transaction_date=mapping["transaction_date"],
        transaction_code=mapping["transaction_code"],
        ad_code=mapping["ad_code"],
        shares=(float(mapping["sum_shares"]) if mapping["sum_shares"] is not None else None),
        price_per_share=(float(price) if price is not None else None),
        transaction_value=(
            float(mapping["sum_value"]) if mapping["sum_value"] is not None else None
        ),
        txn_count=txn_count,
        enriched_phone=mapping["enriched_phone"],
        enriched_email=mapping["enriched_email"],
        enriched_at=mapping["enriched_at"],
        source_filing_url=mapping["source_filing_url"],
        filed_at=mapping["filed_at"],
    )
