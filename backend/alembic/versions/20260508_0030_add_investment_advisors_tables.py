"""Add investment_advisors + advisor_contacts + advisor_filings tables.

Revision ID: 20260508_0030
Revises: 20260507_0029
Create Date: 2026-05-08

Foundation for the Investment Advisor Master List — a sibling workspace to
the existing Broker-Dealer Master List, scoped to SEC-registered investment
advisory firms that file Form 13F. The data model intentionally lives in a
dedicated table rather than extending ``broker_dealers`` because Form ADV
and Form BD share only ~4 columns (CRD, name, city, state); folding the two
shapes together would mean ~30 BD-only columns NULL on every advisor and
~25 ADV-only columns NULL on every BD, plus a polymorphic refactor of
every existing repository/scoring/alerts call site.

Three tables here:

* ``investment_advisors``   — one row per RIA. Mirrors BD shape for shared
  concepts (cik / crd / name / state / website / ...) and adds Form-ADV
  Item 5 columns (regulatory_aum, advisory_activities, client_types,
  Schedule A/B owners) plus denormalized 13F flags (files_13f /
  latest_13f_filing_date) for the hard-scope filter.
* ``advisor_contacts``      — sibling of ``executive_contacts``. Separate
  table (rather than polymorphic on ``executive_contacts``) keeps the BD
  enrichment code unchanged; the duplication is ~150 LOC of repository
  code which is cheaper than refactoring 18 call sites.
* ``advisor_filings``       — sibling of ``filing_alerts``. Tracks
  Form ADV / Form ADV-W / Form 13F-HR amendments per advisor.

Numeric precision for AUM is ``Numeric(20, 2)`` rather than the BD
``Numeric(18, 2)``: BlackRock alone reports ~$11T regulatory AUM and
``Numeric(18, 2)`` caps at $9,999,999,999,999,999.99 (~$10 quadrillion in
hundredths) — fine in theory but tight given that ADV totals can run into
the tens of trillions. ``Numeric(20, 2)`` gives 4 extra digits of headroom.

Indexes are tuned for the planned default sort (``regulatory_aum DESC
NULLS LAST``) and the hard 13F scope filter (``files_13f = TRUE``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "20260508_0030"
down_revision: str | None = "20260507_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investment_advisors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # Identifiers — CIK is unique-but-nullable (some IAPD-only firms have
        # no EDGAR CIK); CRD is the RIA primary key from FINRA but kept
        # nullable for resilience against bulk-import row malformation.
        sa.Column("cik", sa.String(length=32), nullable=True),
        sa.Column("crd_number", sa.String(length=32), nullable=True),
        sa.Column("sec_file_number", sa.String(length=64), nullable=True),
        # Names — ``name`` is the firm's primary business name (Item 1.A);
        # ``legal_name`` is the legal entity name when it differs (Item 1.B).
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        # Principal office (Item 1.F).
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        # Lifecycle dates.
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("formation_date", sa.Date(), nullable=True),
        sa.Column("last_filing_date", sa.Date(), nullable=True),
        sa.Column("filings_index_url", sa.Text(), nullable=True),
        sa.Column(
            "matched_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'iapd'"),
        ),
        sa.Column(
            "status",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # Web presence — populated from Item 1.I.(1) when present, else
        # filled by the same Apollo/Serper/SerpAPI website resolver chain
        # the BD pipeline uses (the resolver's domain blocklist already
        # covers sec.gov and finra.org, so it works as-is for advisors).
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("website_source", sa.String(length=16), nullable=True),
        # Form ADV Item 5 financial disclosures. Numeric(20,2) gives
        # headroom past ``Numeric(18,2)``'s ~$10 quadrillion cap — see
        # module docstring.
        sa.Column("regulatory_aum", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column(
            "discretionary_aum", sa.Numeric(precision=20, scale=2), nullable=True
        ),
        sa.Column(
            "non_discretionary_aum", sa.Numeric(precision=20, scale=2), nullable=True
        ),
        sa.Column("total_clients", sa.Integer(), nullable=True),
        # Form ADV multi-select fields — JSONB arrays, mirroring the BD
        # ``types_of_business`` shape. ``none_as_null=True`` makes Python
        # None writes land as SQL NULL instead of the JSONB scalar 'null'
        # so set-returning aggregators (jsonb_array_elements_text) don't
        # crash on filter-feeder queries — same guardrail BD uses.
        sa.Column(
            "advisory_activities",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "client_types",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "client_counts",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "direct_owners",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "indirect_owners",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "executive_officers",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column("firm_operations_text", sa.Text(), nullable=True),
        # 13F denormalized flags. Storing the boolean + latest-filing date
        # on the advisor row lets the master-list endpoint apply
        # ``WHERE files_13f = TRUE`` without a join, which is the hard
        # filter scope confirmed with the stakeholder.
        sa.Column(
            "files_13f",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("latest_13f_filing_date", sa.Date(), nullable=True),
        # Apollo/Serper enrichment cooldown gate — same semantics as
        # ``broker_dealers.last_enrich_attempt_at``.
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_investment_advisors_name",
        "investment_advisors",
        ["name"],
        unique=False,
    )
    # Partial unique index on CIK (NULL allowed but no two non-null rows
    # may share a CIK). Mirrors ``broker_dealers.cik`` semantics.
    op.create_index(
        "ix_investment_advisors_cik",
        "investment_advisors",
        ["cik"],
        unique=True,
        postgresql_where=sa.text("cik IS NOT NULL"),
    )
    op.create_index(
        "ix_investment_advisors_crd_number",
        "investment_advisors",
        ["crd_number"],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_sec_file_number",
        "investment_advisors",
        ["sec_file_number"],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_state",
        "investment_advisors",
        ["state"],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_status",
        "investment_advisors",
        ["status"],
        unique=False,
    )
    # AUM index ordered DESC NULLS LAST so the default master-list query
    # (sort_by=regulatory_aum, sort_dir=desc) hits an index-ordered scan
    # and skips a separate sort step — same shape used by BD's
    # ``last_audit_report_date`` and ``lead_score`` indexes.
    op.create_index(
        "ix_investment_advisors_regulatory_aum",
        "investment_advisors",
        [sa.text("regulatory_aum DESC NULLS LAST")],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_files_13f",
        "investment_advisors",
        ["files_13f"],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_last_filing_date",
        "investment_advisors",
        ["last_filing_date"],
        unique=False,
    )
    op.create_index(
        "ix_investment_advisors_last_enrich_attempt_at",
        "investment_advisors",
        ["last_enrich_attempt_at"],
        unique=False,
    )

    op.create_table(
        "advisor_contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("advisor_id", sa.Integer(), nullable=False),
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
            ["advisor_id"], ["investment_advisors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_advisor_contacts_advisor_id",
        "advisor_contacts",
        ["advisor_id"],
        unique=False,
    )

    op.create_table(
        "advisor_filings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("advisor_id", sa.Integer(), nullable=False),
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
            ["advisor_id"], ["investment_advisors.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_advisor_filings_dedupe_key"),
    )
    op.create_index(
        "ix_advisor_filings_advisor_id",
        "advisor_filings",
        ["advisor_id"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_filings_form_type",
        "advisor_filings",
        ["form_type"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_filings_filed_at",
        "advisor_filings",
        ["filed_at"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_filings_is_read",
        "advisor_filings",
        ["is_read"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_filings_is_read", table_name="advisor_filings")
    op.drop_index("ix_advisor_filings_filed_at", table_name="advisor_filings")
    op.drop_index("ix_advisor_filings_form_type", table_name="advisor_filings")
    op.drop_index("ix_advisor_filings_advisor_id", table_name="advisor_filings")
    op.drop_table("advisor_filings")

    op.drop_index("ix_advisor_contacts_advisor_id", table_name="advisor_contacts")
    op.drop_table("advisor_contacts")

    op.drop_index(
        "ix_investment_advisors_last_enrich_attempt_at",
        table_name="investment_advisors",
    )
    op.drop_index(
        "ix_investment_advisors_last_filing_date", table_name="investment_advisors"
    )
    op.drop_index("ix_investment_advisors_files_13f", table_name="investment_advisors")
    op.drop_index(
        "ix_investment_advisors_regulatory_aum", table_name="investment_advisors"
    )
    op.drop_index("ix_investment_advisors_status", table_name="investment_advisors")
    op.drop_index("ix_investment_advisors_state", table_name="investment_advisors")
    op.drop_index(
        "ix_investment_advisors_sec_file_number", table_name="investment_advisors"
    )
    op.drop_index(
        "ix_investment_advisors_crd_number", table_name="investment_advisors"
    )
    op.drop_index("ix_investment_advisors_cik", table_name="investment_advisors")
    op.drop_index("ix_investment_advisors_name", table_name="investment_advisors")
    op.drop_table("investment_advisors")
