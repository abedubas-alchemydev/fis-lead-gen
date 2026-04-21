"""Add email extractor tables: extraction_run, discovered_email, email_verification, verification_runs.

Revision ID: 20260422_0011
Revises: 20260421_0010
Create Date: 2026-04-22 00:00:00

Copied from the standalone Email Extractor module's two revisions (78f509b95848 +
a1b2c3d4e5f6), collapsed into one parent revision. Keeps the upstream table names
and index names so the module's own tests and future upstream diffs stay valid.

Tables are fully standalone at this point — no FKs into the parent schema. A
`discovered_emails.bd_id` FK to `broker_dealers.id` is deliberately deferred to
Phase 3 (firm linkage + promotion to executive_contact).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260422_0011"
down_revision: str | None = "20260421_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_name", sa.String(length=120), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("person_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_extraction_run_domain"), "extraction_run", ["domain"], unique=False)
    op.create_index(op.f("ix_extraction_run_pipeline_name"), "extraction_run", ["pipeline_name"], unique=False)
    op.create_index(op.f("ix_extraction_run_status"), "extraction_run", ["status"], unique=False)

    op.create_table(
        "discovered_email",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["extraction_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "email", name="uq_discovered_email_run_email"),
    )
    op.create_index(op.f("ix_discovered_email_email"), "discovered_email", ["email"], unique=False)
    op.create_index(op.f("ix_discovered_email_run_id"), "discovered_email", ["run_id"], unique=False)
    op.create_index(op.f("ix_discovered_email_source"), "discovered_email", ["source"], unique=False)

    op.create_table(
        "email_verification",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discovered_email_id", sa.Integer(), nullable=False),
        sa.Column("syntax_valid", sa.Boolean(), nullable=True),
        sa.Column("mx_record_present", sa.Boolean(), nullable=True),
        sa.Column("smtp_status", sa.String(length=32), nullable=False),
        sa.Column("smtp_message", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["discovered_email_id"], ["discovered_email.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_email_verification_discovered_email_id"),
        "email_verification",
        ["discovered_email_id"],
        unique=False,
    )

    op.create_table(
        "verification_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_verification_runs_status"), "verification_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_verification_runs_status"), table_name="verification_runs")
    op.drop_table("verification_runs")
    op.drop_index(op.f("ix_email_verification_discovered_email_id"), table_name="email_verification")
    op.drop_table("email_verification")
    op.drop_index(op.f("ix_discovered_email_source"), table_name="discovered_email")
    op.drop_index(op.f("ix_discovered_email_run_id"), table_name="discovered_email")
    op.drop_index(op.f("ix_discovered_email_email"), table_name="discovered_email")
    op.drop_table("discovered_email")
    op.drop_index(op.f("ix_extraction_run_status"), table_name="extraction_run")
    op.drop_index(op.f("ix_extraction_run_pipeline_name"), table_name="extraction_run")
    op.drop_index(op.f("ix_extraction_run_domain"), table_name="extraction_run")
    op.drop_table("extraction_run")
