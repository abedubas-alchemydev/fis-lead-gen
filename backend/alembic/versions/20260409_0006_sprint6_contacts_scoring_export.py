"""sprint 6 contacts scoring export

Revision ID: 20260409_0006
Revises: 20260409_0005
Create Date: 2026-04-09 19:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0006"
down_revision = "20260409_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broker_dealers", sa.Column("lead_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("broker_dealers", sa.Column("lead_priority", sa.String(length=16), nullable=True))
    op.create_index("ix_broker_dealers_lead_priority", "broker_dealers", ["lead_priority"])
    op.create_index("ix_broker_dealers_lead_score", "broker_dealers", ["lead_score"])

    op.create_table(
        "executive_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("bd_id", sa.Integer(), sa.ForeignKey("broker_dealers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="sample"),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_executive_contacts_bd_id", "executive_contacts", ["bd_id"])

    op.create_table(
        "scoring_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("settings_key", sa.String(length=64), nullable=False),
        sa.Column("net_capital_growth_weight", sa.Integer(), nullable=False, server_default="35"),
        sa.Column("clearing_arrangement_weight", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("financial_health_weight", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("registration_recency_weight", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("settings_key", name="uq_scoring_settings_key"),
    )
    op.create_index("ix_scoring_settings_settings_key", "scoring_settings", ["settings_key"])

    op.execute(
        """
        INSERT INTO scoring_settings (
            settings_key,
            net_capital_growth_weight,
            clearing_arrangement_weight,
            financial_health_weight,
            registration_recency_weight
        ) VALUES ('default', 35, 30, 20, 15)
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_scoring_settings_settings_key", table_name="scoring_settings")
    op.drop_table("scoring_settings")
    op.drop_index("ix_executive_contacts_bd_id", table_name="executive_contacts")
    op.drop_table("executive_contacts")
    op.drop_index("ix_broker_dealers_lead_score", table_name="broker_dealers")
    op.drop_index("ix_broker_dealers_lead_priority", table_name="broker_dealers")
    op.drop_column("broker_dealers", "lead_priority")
    op.drop_column("broker_dealers", "lead_score")
