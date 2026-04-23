"""financial_metrics unique constraint on (bd_id, report_date)

Revision ID: 2cc4af2a4ef5
Revises: 20260423_0013
Create Date: 2026-04-23 18:13:38.728299

Phase 2C-schema of the fix-everything-then-fresh-start plan. Closes the
H3 latent trap surfaced by the Phase 1B multi-year extraction audit
(reports/multi-year-extraction-audit-2026-04-24.md). The audit showed
that the financial pipeline's DELETE-then-INSERT is scoped to bd_id
alone; once the call-site swap to extract_multi_year_financial_data
lands (Phase 2C-code), the first run that produces fewer rows than a
prior run would destroy older fiscal years. A UNIQUE(bd_id, report_date)
constraint makes the intent durable at the DB level and supports the
narrowed DELETE scope shipping alongside in focus_reports.py.

Upgrade dedupes any existing (bd_id, report_date) duplicates before the
constraint is added. The dedupe keeps the most-recently-inserted row
per pair (ORDER BY created_at DESC, id DESC). financial_metrics has no
confidence_score column today (Fix G / Phase 2D will add it); once that
lands a follow-up can re-rank on confidence if needed. The pre-migration
audit at .tmp/phase2c_schema_audit.pre.log shows zero duplicates on
staging Neon as of 2026-04-23, so the dedupe is expected to be a no-op
there. It still runs unconditionally so the upgrade is safe against any
environment (local, future Neon branches) where duplicates may exist.

Downgrade drops the constraint. Dedupe deletions are not restored on
downgrade -- the cleanup is one-way.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2cc4af2a4ef5"
down_revision = "20260423_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dedupe any (bd_id, report_date) duplicates before adding the UNIQUE.
    # Keep the most-recently-inserted row per pair: ORDER BY created_at DESC,
    # id DESC. financial_metrics has no confidence_score column today, so we
    # cannot rank on confidence; created_at is deterministic and prefers the
    # later pipeline run (which typically benefits from prompt/code fixes).
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY bd_id, report_date
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM financial_metrics
            )
            DELETE FROM financial_metrics
            WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        )
    )

    op.create_unique_constraint(
        "uq_financial_metrics_bd_report_date",
        "financial_metrics",
        ["bd_id", "report_date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_financial_metrics_bd_report_date",
        "financial_metrics",
        type_="unique",
    )
