"""Altinn 3 systembruker, aksjeeierbok, and RF-1086 (aksjonærregisteroppgaven).

Adds eight company-scoped tables:

- ``company_system_user``: Altinn 3 systembruker requests and approvals per company.
- ``share_class``: share classes (aksjeklasser, codes 01..13) with opening capital.
- ``shareholder``: shareholders in the company's aksjeeierbok (aksjeloven § 4-5).
- ``share_opening_holding``: opening share counts per shareholder and class.
- ``share_event`` and ``share_event_line``: corporate and shareholder events.
- ``aksjonaer_filing`` and ``aksjonaer_filing_document``: RF-1086 submission log
  and archived Hovedskjema / Underskjema XML documents.

**What it infers:** nothing. Existing companies keep working without any rows in
these tables until an administrator connects Altinn or starts the aksjeeierbok.

**What it refuses to guess:** share classes, shareholders, opening holdings, or
Altinn system users for existing companies. Each company enters its register or
imports last year's filing via Skatteetaten prefill.

Revision ID: b8e4c2a19d07
Revises: a4c8d1f73b29
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8e4c2a19d07"
down_revision: str | None = "a4c8d1f73b29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "company_system_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("altinn_request_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resources", _JSON_DOC, nullable=False),
        sa.Column("access_packages", _JSON_DOC, nullable=False),
        sa.Column("confirm_url", sa.String(length=512), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("altinn_request_id"),
    )
    op.create_index("ix_company_system_user_company_id", "company_system_user", ["company_id"])

    op.create_table(
        "share_class",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("isin", sa.String(length=35), nullable=True),
        sa.Column("opening_year", sa.Integer(), nullable=True),
        sa.Column("opening_shares", sa.BigInteger(), nullable=False),
        sa.Column("opening_par_value", sa.Numeric(18, 6), nullable=False),
        sa.Column("opening_share_capital", sa.Numeric(38, 2), nullable=False),
        sa.Column("opening_paid_in_capital", sa.Numeric(38, 2), nullable=False),
        sa.Column("opening_paid_in_premium", sa.Numeric(38, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code", name="uq_share_class_company_code"),
    )
    op.create_index("ix_share_class_company_id", "share_class", ["company_id"])

    op.create_table(
        "shareholder",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("national_id", sa.String(length=11), nullable=True),
        sa.Column("orgnr", sa.String(length=9), nullable=True),
        sa.Column("foreign_id", sa.String(length=12), nullable=True),
        sa.Column("address", sa.String(length=105), nullable=True),
        sa.Column("postal_code", sa.String(length=15), nullable=True),
        sa.Column("city", sa.String(length=35), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("email", sa.String(length=150), nullable=True),
        sa.Column("is_own_shares", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shareholder_company_id", "shareholder", ["company_id"])

    op.create_table(
        "share_opening_holding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("share_class_id", sa.Integer(), nullable=False),
        sa.Column("shareholder_id", sa.Integer(), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_class_id"], ["share_class.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shareholder_id"], ["shareholder.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "share_class_id", "shareholder_id", name="uq_share_opening_class_holder"
        ),
    )
    op.create_index("ix_share_opening_holding_company_id", "share_opening_holding", ["company_id"])

    op.create_table(
        "share_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("share_class_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("subtype", sa.String(length=32), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("total_shares", sa.BigInteger(), nullable=True),
        sa.Column("par_value_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("total_amount", sa.Numeric(38, 2), nullable=True),
        sa.Column("premium_amount", sa.Numeric(38, 2), nullable=True),
        sa.Column("amount_per_share", sa.Numeric(18, 6), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_class_id"], ["share_class.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_event_company_date", "share_event", ["company_id", "event_date"])
    op.create_index("ix_share_event_share_class_id", "share_event", ["share_class_id"])

    op.create_table(
        "share_event_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("shareholder_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(38, 2), nullable=True),
        sa.Column("paid_in_premium", sa.Numeric(38, 2), nullable=True),
        sa.Column("withholding_tax", sa.Numeric(38, 2), nullable=True),
        sa.Column("withholding_tax_rate", sa.Integer(), nullable=True),
        sa.Column("withholding_tax_country", sa.String(length=2), nullable=True),
        sa.Column("counterparty_national_id", sa.String(length=11), nullable=True),
        sa.Column("counterparty_orgnr", sa.String(length=9), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["share_event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shareholder_id"], ["shareholder.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_share_event_line_event_id", "share_event_line", ["event_id"])
    op.create_index("ix_share_event_line_company_id", "share_event_line", ["company_id"])

    op.create_table(
        "aksjonaer_filing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("share_class_id", sa.Integer(), nullable=True),
        sa.Column("share_class_code", sa.String(length=2), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hovedskjema_xml", sa.Text(), nullable=False),
        sa.Column("underskjema_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("hovedskjema_id", sa.String(length=80), nullable=True),
        sa.Column("leveranse_referanse", sa.String(length=120), nullable=True),
        sa.Column("dialog_id", sa.String(length=80), nullable=True),
        sa.Column("forsendelse_id", sa.String(length=80), nullable=True),
        sa.Column("contact_email", sa.String(length=150), nullable=True),
        sa.Column("contact_name", sa.String(length=150), nullable=True),
        sa.Column("findings", _JSON_DOC, nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["share_class_id"], ["share_class.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aksjonaer_filing_company_year", "aksjonaer_filing", ["company_id", "year"])

    op.create_table(
        "aksjonaer_filing_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["filing_id"], ["aksjonaer_filing.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_aksjonaer_filing_document_filing_id", "aksjonaer_filing_document", ["filing_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_aksjonaer_filing_document_filing_id", table_name="aksjonaer_filing_document")
    op.drop_table("aksjonaer_filing_document")
    op.drop_index("ix_aksjonaer_filing_company_year", table_name="aksjonaer_filing")
    op.drop_table("aksjonaer_filing")
    op.drop_index("ix_share_event_line_company_id", table_name="share_event_line")
    op.drop_index("ix_share_event_line_event_id", table_name="share_event_line")
    op.drop_table("share_event_line")
    op.drop_index("ix_share_event_share_class_id", table_name="share_event")
    op.drop_index("ix_share_event_company_date", table_name="share_event")
    op.drop_table("share_event")
    op.drop_index("ix_share_opening_holding_company_id", table_name="share_opening_holding")
    op.drop_table("share_opening_holding")
    op.drop_index("ix_shareholder_company_id", table_name="shareholder")
    op.drop_table("shareholder")
    op.drop_index("ix_share_class_company_id", table_name="share_class")
    op.drop_table("share_class")
    op.drop_index("ix_company_system_user_company_id", table_name="company_system_user")
    op.drop_table("company_system_user")
