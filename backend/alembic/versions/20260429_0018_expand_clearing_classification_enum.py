"""expand clearing_classification enum to four-value canonical set

Revision ID: 20260429_0018
Revises: 20260427_0017
Create Date: 2026-04-29

The 2026-04-28 audit (reports/clearing-classification-audit-2026-04-28.md)
found that the regex classifier in services/classification.py had its
self-clearing logic inverted, no omnibus detection, and disagreed with
the parallel LLM extraction in clearing_arrangements.clearing_type.
~119 pages of "unknown" firms in the master list were downstream of
those bugs.

This migration is a DATA-only change. The column itself
(broker_dealers.clearing_classification) is already VARCHAR(32) -- there
is no Postgres ENUM type to ALTER. We only adjust the value set:

  Old (broken)         -> New (canonical)
  --------------------    --------------------
  true_self_clearing      needs_review        (semantically inverted; can't trust)
  introducing             fully_disclosed     (only when current_clearing_partner is set)
  introducing             needs_review        (when no partner is on file)
  unknown                 needs_review        (over-populated by inverted Gate 1)

The new canonical value set written by the LLM-based classifier:
  {fully_disclosed, self_clearing, omnibus, unknown, needs_review}

Existing rows are flagged for re-classification; the next pipeline pass
runs services/clearing_classifier.py against the FINRA firm_operations_text
plus the FOCUS report text to assign a real label. cli-03 follows up
with a full backfill ops task once Deshorn spot-checks the result.

Downgrade is best-effort. The old labels were broken, so a clean reverse
map does not exist. Downgrade resets every row that this migration could
have produced (needs_review / fully_disclosed / self_clearing / omnibus)
back to 'unknown' -- the old fallback bucket. Information is lost on
downgrade by design.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260429_0018"
down_revision: str | None = "20260427_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. true_self_clearing was inverted by the old Gate 1 -- flag for re-classification.
    op.execute(
        """
        UPDATE broker_dealers
        SET clearing_classification = 'needs_review'
        WHERE clearing_classification = 'true_self_clearing'
        """
    )
    # 2. unknown was over-populated by the same inverted Gate 1 -- flag for re-classification.
    op.execute(
        """
        UPDATE broker_dealers
        SET clearing_classification = 'needs_review'
        WHERE clearing_classification = 'unknown'
        """
    )
    # 3. introducing rows: trust the partner where one is on file (fully_disclosed),
    #    otherwise flag for re-classification.
    op.execute(
        """
        UPDATE broker_dealers
        SET clearing_classification = CASE
            WHEN current_clearing_partner IS NOT NULL THEN 'fully_disclosed'
            ELSE 'needs_review'
        END
        WHERE clearing_classification = 'introducing'
        """
    )


def downgrade() -> None:
    # Best-effort. The old three-value set {true_self_clearing, introducing,
    # unknown} cannot losslessly absorb the new five-value set, and the old
    # labels were inverted anyway, so the reverse map is intentionally lossy.
    op.execute(
        """
        UPDATE broker_dealers
        SET clearing_classification = 'unknown'
        WHERE clearing_classification IN (
            'fully_disclosed', 'self_clearing', 'omnibus', 'needs_review'
        )
        """
    )
