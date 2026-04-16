"""add sprint 2 broker dealer fields

Revision ID: 20260409_0002
Revises: 20260409_0001
Create Date: 2026-04-09 00:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0002"
down_revision = "20260409_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_dealers", sa.Column("sec_file_number", sa.String(length=64), nullable=True))
    op.add_column("broker_dealers", sa.Column("branch_count", sa.Integer(), nullable=True))
    op.add_column("broker_dealers", sa.Column("business_type", sa.String(length=120), nullable=True))
    op.add_column("broker_dealers", sa.Column("registration_date", sa.Date(), nullable=True))
    op.add_column(
        "broker_dealers",
        sa.Column("matched_source", sa.String(length=16), nullable=False, server_default="edgar"),
    )
    op.add_column("broker_dealers", sa.Column("last_filing_date", sa.Date(), nullable=True))
    op.add_column("broker_dealers", sa.Column("filings_index_url", sa.Text(), nullable=True))

    op.create_index("ix_broker_dealers_crd_number", "broker_dealers", ["crd_number"])
    op.create_index("ix_broker_dealers_sec_file_number", "broker_dealers", ["sec_file_number"])


def downgrade() -> None:
    op.drop_index("ix_broker_dealers_sec_file_number", table_name="broker_dealers")
    op.drop_index("ix_broker_dealers_crd_number", table_name="broker_dealers")
    op.drop_column("broker_dealers", "filings_index_url")
    op.drop_column("broker_dealers", "last_filing_date")
    op.drop_column("broker_dealers", "matched_source")
    op.drop_column("broker_dealers", "registration_date")
    op.drop_column("broker_dealers", "business_type")
    op.drop_column("broker_dealers", "branch_count")
    op.drop_column("broker_dealers", "sec_file_number")
