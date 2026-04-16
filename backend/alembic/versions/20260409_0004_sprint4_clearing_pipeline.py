"""sprint 4 clearing pipeline

Revision ID: 20260409_0004
Revises: 20260409_0003
Create Date: 2026-04-09 00:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0004"
down_revision = "20260409_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_dealers", sa.Column("current_clearing_partner", sa.String(length=255), nullable=True))
    op.add_column("broker_dealers", sa.Column("current_clearing_type", sa.String(length=32), nullable=True))
    op.add_column(
        "broker_dealers",
        sa.Column("current_clearing_is_competitor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("broker_dealers", sa.Column("current_clearing_source_filing_url", sa.Text(), nullable=True))
    op.add_column("broker_dealers", sa.Column("current_clearing_extraction_confidence", sa.Numeric(5, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("last_audit_report_date", sa.Date(), nullable=True))
    op.create_index("ix_broker_dealers_current_clearing_partner", "broker_dealers", ["current_clearing_partner"])
    op.create_index("ix_broker_dealers_current_clearing_type", "broker_dealers", ["current_clearing_type"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pipeline_name", sa.String(length=120), nullable=False),
        sa.Column("trigger_source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pipeline_runs_pipeline_name", "pipeline_runs", ["pipeline_name"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])

    op.create_table(
        "competitor_providers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("name", name="uq_competitor_providers_name"),
    )
    op.create_index("ix_competitor_providers_name", "competitor_providers", ["name"])

    op.create_table(
        "clearing_arrangements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bd_id", sa.Integer(), sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("filing_year", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column("source_pdf_url", sa.Text(), nullable=True),
        sa.Column("local_document_path", sa.Text(), nullable=True),
        sa.Column("clearing_partner", sa.String(length=255), nullable=True),
        sa.Column("normalized_partner", sa.String(length=255), nullable=True),
        sa.Column("clearing_type", sa.String(length=32), nullable=True),
        sa.Column("agreement_date", sa.Date(), nullable=True),
        sa.Column("extraction_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("extraction_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("extraction_notes", sa.Text(), nullable=True),
        sa.Column("is_competitor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("bd_id", "filing_year", name="uq_clearing_arrangements_bd_year"),
    )
    op.create_index("ix_clearing_arrangements_bd_id", "clearing_arrangements", ["bd_id"])
    op.create_index("ix_clearing_arrangements_clearing_partner", "clearing_arrangements", ["clearing_partner"])
    op.create_index("ix_clearing_arrangements_clearing_type", "clearing_arrangements", ["clearing_type"])
    op.create_index("ix_clearing_arrangements_extraction_status", "clearing_arrangements", ["extraction_status"])
    op.create_index("ix_clearing_arrangements_filing_year", "clearing_arrangements", ["filing_year"])
    op.create_index("ix_clearing_arrangements_is_competitor", "clearing_arrangements", ["is_competitor"])


def downgrade() -> None:
    op.drop_index("ix_clearing_arrangements_is_competitor", table_name="clearing_arrangements")
    op.drop_index("ix_clearing_arrangements_filing_year", table_name="clearing_arrangements")
    op.drop_index("ix_clearing_arrangements_extraction_status", table_name="clearing_arrangements")
    op.drop_index("ix_clearing_arrangements_clearing_type", table_name="clearing_arrangements")
    op.drop_index("ix_clearing_arrangements_clearing_partner", table_name="clearing_arrangements")
    op.drop_index("ix_clearing_arrangements_bd_id", table_name="clearing_arrangements")
    op.drop_table("clearing_arrangements")
    op.drop_index("ix_competitor_providers_name", table_name="competitor_providers")
    op.drop_table("competitor_providers")
    op.drop_index("ix_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_pipeline_name", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_broker_dealers_current_clearing_type", table_name="broker_dealers")
    op.drop_index("ix_broker_dealers_current_clearing_partner", table_name="broker_dealers")
    op.drop_column("broker_dealers", "last_audit_report_date")
    op.drop_column("broker_dealers", "current_clearing_extraction_confidence")
    op.drop_column("broker_dealers", "current_clearing_source_filing_url")
    op.drop_column("broker_dealers", "current_clearing_is_competitor")
    op.drop_column("broker_dealers", "current_clearing_type")
    op.drop_column("broker_dealers", "current_clearing_partner")
