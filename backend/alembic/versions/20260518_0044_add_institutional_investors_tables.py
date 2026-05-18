"""Add institutional_investors + investor_contacts + investor_filings tables.

Revision ID: 20260518_0044
Revises: 20260518_0043
Create Date: 2026-05-18

Foundation for the new Institutional Investors list — a third firm-style
workspace alongside Master BD and Investment Advisors, scoped to 13F-HR
filers (institutional investment managers with >=$100M in qualified
securities). Many 13F filers are also registered investment advisers
(same CIK appears in both IAPD and EDGAR 13F filings); the
``advisor_id`` FK on this table is populated for that overlap subset.
Pure-13F-only filers (private funds, pensions, insurance companies) have
no IAPD row and live here with ``advisor_id = NULL``.

The denormalized ``advisor_id`` lets the FE render a "Also a registered
investment adviser ->" link without a name-based join, and lets the
``institutional_investors`` row pull display data (regulatory_aum, etc.)
back from the advisor row when present.

Three tables here, mirroring the advisor-side ``investment_advisors``
shape from migration 0030:

* ``institutional_investors`` -- one row per 13F-filer CIK. AUM is
  ``total_aum`` (parsed from 13F-HR ``<tableValueTotal>``) so pure-13F
  filers have a number even when no IAPD ``regulatory_aum`` exists.
* ``investor_contacts``        -- sibling of ``advisor_contacts``.
* ``investor_filings``         -- sibling of ``advisor_filings``,
  tracks Form 13F-HR amendments + Schedule 13D/G + other holdings-side
  filings.

The seed at the end of ``upgrade`` populates the table from existing
advisors flagged ``files_13f = TRUE`` so the new list lights up on first
deploy. Pure-13F CIKs (no advisor) land via the 13F monitor pipeline.
Backfill is idempotent (``ON CONFLICT DO NOTHING`` on the partial unique
index over CIK).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260518_0044"
down_revision: str | None = "20260518_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "institutional_investors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # CIK is the natural key for 13F filers (every 13F-HR carries one).
        # Nullable + partial-unique so the table can host a manually
        # curated investor row (e.g. unregistered family office) before
        # its CIK is known.
        sa.Column("cik", sa.String(length=32), nullable=True),
        # Cross-link to the advisor row when the same CIK is also a
        # registered RIA. ON DELETE SET NULL so removing an advisor
        # doesn't cascade-kill the investor record (the 13F filing
        # history is independent).
        sa.Column("advisor_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("website_source", sa.String(length=16), nullable=True),
        sa.Column("latest_13f_filing_date", sa.Date(), nullable=True),
        # Total holdings value parsed from the latest 13F-HR
        # ``<tableValueTotal>`` element. Numeric(20,2) for parity with
        # advisor ``regulatory_aum`` headroom (BlackRock-scale).
        sa.Column("total_aum", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("holdings_count", sa.Integer(), nullable=True),
        sa.Column("filings_index_url", sa.Text(), nullable=True),
        sa.Column(
            "matched_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'edgar_13f'"),
        ),
        sa.Column(
            "status",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "last_enrich_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
            ["advisor_id"], ["investment_advisors.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_institutional_investors_name",
        "institutional_investors",
        ["name"],
        unique=False,
    )
    # Partial unique index on CIK (NULL allowed but no two non-null rows
    # may share a CIK). Mirrors ``investment_advisors.cik`` semantics.
    op.create_index(
        "ix_institutional_investors_cik",
        "institutional_investors",
        ["cik"],
        unique=True,
        postgresql_where=sa.text("cik IS NOT NULL"),
    )
    op.create_index(
        "ix_institutional_investors_advisor_id",
        "institutional_investors",
        ["advisor_id"],
        unique=False,
    )
    op.create_index(
        "ix_institutional_investors_state",
        "institutional_investors",
        ["state"],
        unique=False,
    )
    op.create_index(
        "ix_institutional_investors_status",
        "institutional_investors",
        ["status"],
        unique=False,
    )
    # Default sort scope: total_aum DESC NULLS LAST. Matches the AUM-
    # ranked default the advisor list uses on regulatory_aum.
    op.create_index(
        "ix_institutional_investors_total_aum",
        "institutional_investors",
        [sa.text("total_aum DESC NULLS LAST")],
        unique=False,
    )
    op.create_index(
        "ix_institutional_investors_latest_13f_filing_date",
        "institutional_investors",
        ["latest_13f_filing_date"],
        unique=False,
    )
    op.create_index(
        "ix_institutional_investors_last_enrich_attempt_at",
        "institutional_investors",
        ["last_enrich_attempt_at"],
        unique=False,
    )

    op.create_table(
        "investor_contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'provider'"),
        ),
        sa.Column("discovery_source", sa.String(length=32), nullable=True),
        sa.Column("discovery_confidence", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "enriched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
            ["investor_id"], ["institutional_investors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investor_contacts_investor_id",
        "investor_contacts",
        ["investor_id"],
        unique=False,
    )

    op.create_table(
        "investor_filings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investor_id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("form_type", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_filing_url", sa.Text(), nullable=True),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            ["investor_id"], ["institutional_investors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_investor_filings_dedupe_key"),
    )
    op.create_index(
        "ix_investor_filings_investor_id",
        "investor_filings",
        ["investor_id"],
        unique=False,
    )
    op.create_index(
        "ix_investor_filings_form_type",
        "investor_filings",
        ["form_type"],
        unique=False,
    )
    op.create_index(
        "ix_investor_filings_filed_at",
        "investor_filings",
        ["filed_at"],
        unique=False,
    )
    op.create_index(
        "ix_investor_filings_is_read",
        "investor_filings",
        ["is_read"],
        unique=False,
    )

    # Idempotent seed: every advisor flagged files_13f = TRUE becomes an
    # institutional_investors row with its advisor_id cross-link
    # pre-populated. ON CONFLICT against the partial unique index over
    # CIK keeps re-runs (and an unfortunate downgrade-then-upgrade)
    # from doubling rows. matched_source 'edgar_13f' is the default;
    # rows seeded from an existing advisor get 'iapd_13f' to disclose
    # provenance.
    op.execute(
        """
        INSERT INTO institutional_investors (
            cik, advisor_id, name, legal_name, city, state, website,
            website_source, latest_13f_filing_date, filings_index_url,
            matched_source, status, created_at, updated_at
        )
        SELECT
            ia.cik, ia.id, ia.name, ia.legal_name, ia.city, ia.state,
            ia.website, ia.website_source, ia.latest_13f_filing_date,
            ia.filings_index_url, 'iapd_13f', 'pending', now(), now()
        FROM investment_advisors ia
        WHERE ia.files_13f = TRUE
          AND ia.cik IS NOT NULL
        ON CONFLICT (cik) WHERE cik IS NOT NULL DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_investor_filings_is_read", table_name="investor_filings")
    op.drop_index("ix_investor_filings_filed_at", table_name="investor_filings")
    op.drop_index("ix_investor_filings_form_type", table_name="investor_filings")
    op.drop_index("ix_investor_filings_investor_id", table_name="investor_filings")
    op.drop_table("investor_filings")

    op.drop_index("ix_investor_contacts_investor_id", table_name="investor_contacts")
    op.drop_table("investor_contacts")

    op.drop_index(
        "ix_institutional_investors_last_enrich_attempt_at",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_latest_13f_filing_date",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_total_aum",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_status",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_state",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_advisor_id",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_cik",
        table_name="institutional_investors",
    )
    op.drop_index(
        "ix_institutional_investors_name",
        table_name="institutional_investors",
    )
    op.drop_table("institutional_investors")
