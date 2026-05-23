"""Add clearing_agency_memberships table + checked_at sentinels.

Revision ID: 20260523_0059
Revises: 20260522_0058
Create Date: 2026-05-23

New feature: label each firm with the clearing agencies / SROs it is a
member of (OCC, DTC, NSCC, FICC-GOV, FICC-MBS), sourced from the OCC/DTCC
member directories and matched to firms by normalized name.

Two changes:

1. New ``clearing_agency_memberships`` table — polymorphic across
   broker-dealers and investment advisers via two nullable FK columns +
   an XOR check (the ``favorite_list_item`` pattern). Carries provenance
   (member number, source file, match method/confidence) and a status so
   ambiguous name matches land as ``needs_review`` instead of auto-applying.
   Two partial unique indexes (one per firm side) enforce one row per
   (firm, agency) and are the importer's upsert conflict targets.
2. ``clearing_membership_checked_at`` on ``broker_dealers`` and
   ``investment_advisors`` — the sentinel that tells "evaluated, not a
   member" apart from "never checked".

Distinct from the existing ``clearing_arrangements`` (per-filing clearing
partner) and ``clearing_classification`` (self/fully-disclosed/omnibus).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260523_0059"
down_revision: str | None = "20260522_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clearing_agency_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("broker_dealer_id", sa.Integer(), nullable=True),
        sa.Column("advisor_id", sa.Integer(), nullable=True),
        sa.Column("agency", sa.String(length=16), nullable=False),
        sa.Column("member_number", sa.String(length=64), nullable=True),
        sa.Column("member_name_raw", sa.String(length=255), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=True),
        sa.Column("match_method", sa.String(length=24), nullable=False),
        sa.Column("match_confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pipeline_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["broker_dealer_id"],
            ["broker_dealers.id"],
            name="fk_clearing_agency_membership_broker_dealer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["advisor_id"],
            ["investment_advisors.id"],
            name="fk_clearing_agency_membership_advisor_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_clearing_agency_membership_pipeline_run_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(CASE WHEN broker_dealer_id IS NOT NULL THEN 1 ELSE 0 END "
            "+ CASE WHEN advisor_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_clearing_agency_membership_one_firm",
        ),
        sa.CheckConstraint(
            "agency IN ('OCC', 'DTC', 'NSCC', 'FICC-GOV', 'FICC-MBS')",
            name="ck_clearing_agency_membership_agency",
        ),
        sa.CheckConstraint(
            "match_method IN ('exact_normalized', 'dba', 'alias', 'manual')",
            name="ck_clearing_agency_membership_match_method",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'needs_review', 'rejected')",
            name="ck_clearing_agency_membership_status",
        ),
    )
    op.create_index(
        "ix_clearing_agency_memberships_broker_dealer_id",
        "clearing_agency_memberships",
        ["broker_dealer_id"],
        unique=False,
    )
    op.create_index(
        "ix_clearing_agency_memberships_advisor_id",
        "clearing_agency_memberships",
        ["advisor_id"],
        unique=False,
    )
    op.create_index(
        "ix_clearing_agency_memberships_agency",
        "clearing_agency_memberships",
        ["agency"],
        unique=False,
    )
    op.create_index(
        "ix_clearing_agency_memberships_status",
        "clearing_agency_memberships",
        ["status"],
        unique=False,
    )
    # One membership per (firm, agency). Partial-per-side because the firm FK
    # is nullable and Postgres treats NULLs as distinct. These are the
    # importer's ON CONFLICT upsert targets.
    op.create_index(
        "uq_clearing_membership_bd_agency",
        "clearing_agency_memberships",
        ["broker_dealer_id", "agency"],
        unique=True,
        postgresql_where=sa.text("broker_dealer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_clearing_membership_advisor_agency",
        "clearing_agency_memberships",
        ["advisor_id", "agency"],
        unique=True,
        postgresql_where=sa.text("advisor_id IS NOT NULL"),
    )

    op.add_column(
        "broker_dealers",
        sa.Column("clearing_membership_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_broker_dealers_clearing_membership_checked_at",
        "broker_dealers",
        ["clearing_membership_checked_at"],
        unique=False,
    )
    op.add_column(
        "investment_advisors",
        sa.Column("clearing_membership_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_investment_advisors_clearing_membership_checked_at",
        "investment_advisors",
        ["clearing_membership_checked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_investment_advisors_clearing_membership_checked_at",
        table_name="investment_advisors",
    )
    op.drop_column("investment_advisors", "clearing_membership_checked_at")
    op.drop_index(
        "ix_broker_dealers_clearing_membership_checked_at",
        table_name="broker_dealers",
    )
    op.drop_column("broker_dealers", "clearing_membership_checked_at")

    op.drop_index(
        "uq_clearing_membership_advisor_agency",
        table_name="clearing_agency_memberships",
    )
    op.drop_index(
        "uq_clearing_membership_bd_agency",
        table_name="clearing_agency_memberships",
    )
    op.drop_index(
        "ix_clearing_agency_memberships_status",
        table_name="clearing_agency_memberships",
    )
    op.drop_index(
        "ix_clearing_agency_memberships_agency",
        table_name="clearing_agency_memberships",
    )
    op.drop_index(
        "ix_clearing_agency_memberships_advisor_id",
        table_name="clearing_agency_memberships",
    )
    op.drop_index(
        "ix_clearing_agency_memberships_broker_dealer_id",
        table_name="clearing_agency_memberships",
    )
    op.drop_table("clearing_agency_memberships")
