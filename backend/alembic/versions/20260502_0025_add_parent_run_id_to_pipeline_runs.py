"""Add parent_run_id self-FK to pipeline_runs.

Revision ID: 20260502_0025
Revises: 20260502_0024
Create Date: 2026-05-02

Per-firm "Refresh-all" orchestrator (POST /broker-dealers/{id}/refresh-all)
spawns up to four sub-pipelines as child PipelineRun rows, all linked
back to a single parent row via ``parent_run_id``. The FE polls the
parent's status; the parent's ``notes.children`` carries each child's
verbatim outcome for the toast summary.

Self-FK with ON DELETE CASCADE so deleting a parent run wipes its
children atomically (matters when an admin truncates the table for a
fresh regen — children-without-parents would be orphan litter).
Indexed because the orchestrator + monitoring queries pull "all
children of run X" by parent_run_id.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260502_0025"
down_revision: str | None = "20260502_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pipeline_runs_parent_run_id",
        source_table="pipeline_runs",
        referent_table="pipeline_runs",
        local_cols=["parent_run_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_pipeline_runs_parent_run_id",
        "pipeline_runs",
        ["parent_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_parent_run_id", table_name="pipeline_runs")
    op.drop_constraint("fk_pipeline_runs_parent_run_id", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "parent_run_id")
