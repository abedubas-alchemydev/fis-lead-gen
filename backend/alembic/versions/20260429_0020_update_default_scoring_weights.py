"""update default scoring weights for ACG ICP

Revision ID: 20260429_0020
Revises: 20260429_0019
Create Date: 2026-04-29

Data-only migration. No schema change.

Re-weights the four ``scoring_settings`` weight columns on the row where
``settings_key = 'default'`` so the default Hot/Warm/Cold composite formula
encodes ACG's Ideal Customer Profile (issue #21):

  * clearing_arrangement_weight  30 -> 60
        (drives competitor_match + classification, ACG primary signal)
  * financial_health_weight      20 -> 15  (drives net_capital)
  * net_capital_growth_weight    35 -> 10  (drives filing_recency)
  * registration_recency_weight  15 -> 15  (drives firm_size + finra_status)
                                  ----
                                  100 bps  (unchanged)

Conceptual six-component breakdown encoded by these four buckets and the
fixed sub-weights inside ``services/scoring.py``:

    competitor_match  0.40   classification    0.20
    net_capital       0.15   filing_recency    0.10
    firm_size         0.10   finra_status      0.05    total = 1.00

Downgrade restores the seed defaults from migration
``20260409_0006_sprint6_contacts_scoring_export``: 35/30/20/15.

Custom rows (settings_key != 'default') are not modified.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260429_0020"
down_revision: str | None = "20260429_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE scoring_settings
        SET clearing_arrangement_weight   = 60,
            financial_health_weight       = 15,
            net_capital_growth_weight     = 10,
            registration_recency_weight   = 15,
            updated_at                    = NOW()
        WHERE settings_key = 'default'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE scoring_settings
        SET clearing_arrangement_weight   = 30,
            financial_health_weight       = 20,
            net_capital_growth_weight     = 35,
            registration_recency_weight   = 15,
            updated_at                    = NOW()
        WHERE settings_key = 'default'
        """
    )
