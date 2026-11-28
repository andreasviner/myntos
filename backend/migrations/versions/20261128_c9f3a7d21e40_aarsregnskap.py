"""Add årsregnskap (RR-0002) draft, filing, and archived document tables.

What this migration creates:

- ``aarsregnskap_draft``: per-company, per-year editable settings, prior-year
  comparative figure overrides, mandatory small-company notes, and board /
  general-meeting approval metadata.
- ``aarsregnskap_filing``: per-submission attempt against Brønnøysundregistrene's
  Altinn 3 app (``brg/aarsregnskap-vanlig-202406``), tracking Altinn instance id,
  lock/sign lifecycle, validation issues, and generated XML snapshots.
- ``aarsregnskap_filing_document``: five-year archive references to stored XML,
  attached PDFs, and Altinn receipt PDFs / signature metadata.

Nothing is inferred or backfilled from existing tables: companies start with
empty draft and filing tables and compute their annual accounts live from the
ledger on first visit.

Revision ID: c9f3a7d21e40
Revises: b8e4c2a19d07
Create Date: 2026-11-28 00:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f3a7d21e40"
down_revision: str | Sequence[str] | None = "b8e4c2a19d07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_MONEY = sa.Numeric(38, 2)


def upgrade() -> None:
    op.create_table(
        "aarsregnskap_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("small_company", sa.Boolean(), nullable=False),
        sa.Column("is_parent", sa.Boolean(), nullable=False),
        sa.Column("consolidated", sa.Boolean(), nullable=False),
        sa.Column("winding_up_accounts", sa.Boolean(), nullable=False),
        sa.Column("auditor_elected_out", sa.Boolean(), nullable=False),
        sa.Column("prepared_by_external_accountant", sa.Boolean(), nullable=False),
        sa.Column("external_accountant_assistance", sa.Boolean(), nullable=False),
        sa.Column("approval_date", sa.Date(), nullable=True),
        sa.Column("confirming_representative", sa.String(length=70), nullable=True),
        sa.Column("contact_email", sa.String(length=70), nullable=True),
        sa.Column("board_meeting_date", sa.Date(), nullable=True),
        sa.Column("board_place", sa.String(length=120), nullable=True),
        sa.Column("board_members", _JSON, nullable=False),
        sa.Column("general_meeting_date", sa.Date(), nullable=True),
        sa.Column("general_meeting_place", sa.String(length=120), nullable=True),
        sa.Column("general_meeting_chair", sa.String(length=120), nullable=True),
        sa.Column("general_meeting_cosigner", sa.String(length=120), nullable=True),
        sa.Column("dividend_proposed", _MONEY, nullable=False),
        sa.Column("prior_year_overrides", _JSON, nullable=False),
        sa.Column("notes", _JSON, nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("company_id", "year", name="uq_aarsregnskap_draft_co_year"),
    )
    op.create_index("ix_aarsregnskap_draft_co_id", "aarsregnskap_draft", ["company_id"])

    op.create_table(
        "aarsregnskap_filing",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("auth_method", sa.String(length=24), nullable=False),
        sa.Column("altinn_instance_id", sa.String(length=128), nullable=True),
        sa.Column("hovedskjema_data_id", sa.String(length=80), nullable=True),
        sa.Column("underskjema_data_id", sa.String(length=80), nullable=True),
        sa.Column("altinn_portal_url", sa.String(length=512), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("aarsresultat", _MONEY, nullable=False),
        sa.Column("sum_eiendeler", _MONEY, nullable=False),
        sa.Column("sum_egenkapital", _MONEY, nullable=False),
        sa.Column("fastsettelsedato", sa.Date(), nullable=True),
        sa.Column("bekreftende_representant", sa.String(length=70), nullable=True),
        sa.Column("hovedskjema_xml", sa.Text(), nullable=False),
        sa.Column("underskjema_xml", sa.Text(), nullable=False),
        sa.Column("validation_issues", _JSON, nullable=False),
        sa.Column("findings", _JSON, nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "signed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("signed_by_name", sa.String(length=150), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
    )
    op.create_index("ix_aarsregnskap_filing_co_year", "aarsregnskap_filing", ["company_id", "year"])

    op.create_table(
        "aarsregnskap_filing_document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "filing_id",
            sa.Integer(),
            sa.ForeignKey("aarsregnskap_filing.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("external_document_id", sa.String(length=80), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("storage_key", name="uq_aarsregnskap_filing_document_storage_key"),
    )
    op.create_index(
        "ix_aarsregnskap_doc_filing_id",
        "aarsregnskap_filing_document",
        ["filing_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_aarsregnskap_doc_filing_id", table_name="aarsregnskap_filing_document")
    op.drop_table("aarsregnskap_filing_document")
    op.drop_index("ix_aarsregnskap_filing_co_year", table_name="aarsregnskap_filing")
    op.drop_table("aarsregnskap_filing")
    op.drop_index("ix_aarsregnskap_draft_co_id", table_name="aarsregnskap_draft")
    op.drop_table("aarsregnskap_draft")
