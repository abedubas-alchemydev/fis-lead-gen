"""add sprint 3 financial metrics

Revision ID: 20260409_0003
Revises: 20260409_0002
Create Date: 2026-04-09 01:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0003"
down_revision = "20260409_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_dealers", sa.Column("required_min_capital", sa.Numeric(18, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("latest_net_capital", sa.Numeric(18, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("latest_excess_net_capital", sa.Numeric(18, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("latest_total_assets", sa.Numeric(18, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("yoy_growth", sa.Numeric(8, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("health_status", sa.String(length=32), nullable=True))

    op.create_table(
        "financial_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bd_id", sa.BigInteger(), sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("net_capital", sa.Numeric(18, 2), nullable=False),
        sa.Column("excess_net_capital", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_assets", sa.Numeric(18, 2), nullable=True),
        sa.Column("required_min_capital", sa.Numeric(18, 2), nullable=True),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_financial_metrics_bd_id", "financial_metrics", ["bd_id"])
    op.create_index("ix_financial_metrics_report_date", "financial_metrics", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_financial_metrics_report_date", table_name="financial_metrics")
    op.drop_index("ix_financial_metrics_bd_id", table_name="financial_metrics")
    op.drop_table("financial_metrics")
    op.drop_column("broker_dealers", "health_status")
    op.drop_column("broker_dealers", "yoy_growth")
    op.drop_column("broker_dealers", "latest_total_assets")
    op.drop_column("broker_dealers", "latest_excess_net_capital")
    op.drop_column("broker_dealers", "latest_net_capital")
    op.drop_column("broker_dealers", "required_min_capital")
