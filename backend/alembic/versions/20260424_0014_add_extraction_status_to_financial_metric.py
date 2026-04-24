"""add extraction_status to financial_metric

Revision ID: 20260424_0014
Revises: 2cc4af2a4ef5
Create Date: 2026-04-24

Phase 2D (Fix G) of the fix-everything-then-fresh-start plan. Brings the
financial side into symmetry with the clearing pipeline, which already
carries an ``extraction_status`` column on ``clearing_arrangements`` (added
in ``20260409_0004_sprint4_clearing_pipeline``). The column shape below
mirrors clearing exactly: ``VARCHAR(32)``, NOT NULL, ``server_default='pending'``,
and a btree index so review-queue queries stay cheap.

Backfill decision: every pre-existing row lands as ``'parsed'``. We can't
retroactively reconstruct what the LLM confidence was on the original
extraction because ``financial_metrics`` never stored a confidence column;
the threshold may also have drifted since those rows were inserted. Marking
history as ``'parsed'`` keeps the review queue honest for new inserts from
post-migration code (which tags via ``classify_financial_extraction_status``)
without fabricating review tasks against rows nobody can re-evaluate.
Clearing's vocabulary (``'parsed'`` for success, not ``'success'``) is reused
verbatim so both pipelines speak the same language.

Downgrade drops the index first, then the column.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260424_0014"
down_revision = "2cc4af2a4ef5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the column. ``server_default='pending'`` fills every existing
    #    row with 'pending' during the add_column step. The default is also
    #    the fallback for any future INSERT that omits the column.
    op.add_column(
        "financial_metrics",
        sa.Column(
            "extraction_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )

    # 2. Backfill existing rows to 'parsed'. See module docstring for the
    #    rationale -- we mark history as 'parsed' rather than retro-classifying
    #    against a confidence threshold we never stored.
    op.execute(
        sa.text("UPDATE financial_metrics SET extraction_status = 'parsed'")
    )

    # 3. Index the column so ``WHERE extraction_status = 'needs_review'``
    #    stays cheap as the table grows. Matches clearing's
    #    ``ix_clearing_arrangements_extraction_status``.
    op.create_index(
        "ix_financial_metrics_extraction_status",
        "financial_metrics",
        ["extraction_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_financial_metrics_extraction_status",
        table_name="financial_metrics",
    )
    op.drop_column("financial_metrics", "extraction_status")
