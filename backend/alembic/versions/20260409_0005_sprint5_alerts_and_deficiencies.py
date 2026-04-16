"""sprint 5 alerts and deficiencies

Revision ID: 20260409_0005
Revises: 20260409_0004
Create Date: 2026-04-09 18:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0005"
down_revision = "20260409_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_dealers",
        sa.Column("is_deficient", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("broker_dealers", sa.Column("latest_deficiency_filed_at", sa.Date(), nullable=True))
    op.create_index("ix_broker_dealers_is_deficient", "broker_dealers", ["is_deficient"])

    op.create_table(
        "filing_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bd_id", sa.Integer(), sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("form_type", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("dedupe_key", name="uq_filing_alerts_dedupe_key"),
    )
    op.create_index("ix_filing_alerts_bd_id", "filing_alerts", ["bd_id"])
    op.create_index("ix_filing_alerts_dedupe_key", "filing_alerts", ["dedupe_key"])
    op.create_index("ix_filing_alerts_filed_at", "filing_alerts", ["filed_at"])
    op.create_index("ix_filing_alerts_form_type", "filing_alerts", ["form_type"])
    op.create_index("ix_filing_alerts_is_read", "filing_alerts", ["is_read"])
    op.create_index("ix_filing_alerts_priority", "filing_alerts", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_filing_alerts_priority", table_name="filing_alerts")
    op.drop_index("ix_filing_alerts_is_read", table_name="filing_alerts")
    op.drop_index("ix_filing_alerts_form_type", table_name="filing_alerts")
    op.drop_index("ix_filing_alerts_filed_at", table_name="filing_alerts")
    op.drop_index("ix_filing_alerts_dedupe_key", table_name="filing_alerts")
    op.drop_index("ix_filing_alerts_bd_id", table_name="filing_alerts")
    op.drop_table("filing_alerts")
    op.drop_index("ix_broker_dealers_is_deficient", table_name="broker_dealers")
    op.drop_column("broker_dealers", "latest_deficiency_filed_at")
    op.drop_column("broker_dealers", "is_deficient")
