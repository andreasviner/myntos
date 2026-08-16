"""Let a seller say whether Myntos should assign the KID itself.

The KID carries the invoice number (see ``myntos.domain.faktura.kid``), so it
cannot be written on a draft — the number does not exist yet. What the draft can
carry is the *decision*: assign one at issue, or leave the payment reference to
the seller. That is this column.

``true`` for existing rows, which is both the model's default and the right
answer for them: every row already in the table is either a draft that has not
been issued — and would want a KID like any other — or an issued document, where
the flag is read by nothing, because issue has already been and gone.

Added with ``server_default`` so existing rows get a value, then dropped: the
model declares a Python-side default only, and a lingering server default shows
up as a phantom diff on every later autogenerate (see revision 7b4e0d92a1c8).

Revision ID: e2b70c5a4d19
Revises: c4a91f60b2d7
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "e2b70c5a4d19"
down_revision: str | None = "c4a91f60b2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "payment_reference_auto"

# Created separately in revision 7b4e0d92a1c8, so it is not part of the table
# definition below — and a SQLite batch rebuild drops what the definition does
# not mention. Declared here so the unique index survives this migration; on
# PostgreSQL nothing is rebuilt and it is a no-op either way.
CREDIT_INDEX = "uq_sales_document_credits"


def _sales_document_table(*extra: sa.Column[object]) -> sa.Table:
    """``sales_document`` as of the previous revision, plus anything passed in.

    Spelled out rather than reflected so the table SQLite rebuilds keeps the
    constraint names the models declare — the same reasoning as revisions
    3d7c15ba90e4, 7b4e0d92a1c8 and c4a91f60b2d7.
    """
    return sa.Table(
        "sales_document",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "document_type",
            sa.Enum("faktura", "kreditnota", name="sales_document_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("utkast", "utstedt", name="sales_document_status"),
            nullable=False,
        ),
        sa.Column("document_number", sa.String(length=32), nullable=True),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("buyer_name", sa.String(length=255), nullable=False),
        sa.Column("buyer_orgnr", sa.String(length=9), nullable=True),
        sa.Column("buyer_address_line", sa.String(length=255), nullable=True),
        sa.Column("buyer_postal_code", sa.String(length=16), nullable=True),
        sa.Column("buyer_city", sa.String(length=128), nullable=True),
        sa.Column("buyer_country", sa.String(length=2), nullable=False),
        sa.Column("buyer_is_business", sa.Boolean(), nullable=False),
        sa.Column("seller_name", sa.String(length=255), nullable=False),
        sa.Column("seller_orgnr", sa.String(length=9), nullable=False),
        sa.Column("seller_address_line", sa.String(length=255), nullable=True),
        sa.Column("seller_postal_code", sa.String(length=16), nullable=True),
        sa.Column("seller_city", sa.String(length=128), nullable=True),
        sa.Column("seller_country", sa.String(length=2), nullable=False),
        sa.Column("seller_vat_registered", sa.Boolean(), nullable=False),
        sa.Column("seller_in_foretaksregisteret", sa.Boolean(), nullable=False),
        sa.Column("seller_organisation_form", sa.String(length=16), nullable=True),
        sa.Column("seller_bank_account", sa.String(length=34), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("delivery_date", sa.Date(), nullable=True),
        sa.Column("delivery_place", sa.String(length=255), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("payment_reference", sa.String(length=32), nullable=True),
        *extra,
        sa.Column("buyer_reference", sa.String(length=128), nullable=True),
        sa.Column("order_reference", sa.String(length=128), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("reverse_charge", sa.Boolean(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("credits_document_pk", sa.Integer(), nullable=True),
        sa.Column("ehf_xml", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'utkast' AND document_number IS NULL AND issued_at IS NULL) "
            "OR (status = 'utstedt' AND document_number IS NOT NULL AND issued_at IS NOT NULL)",
            name="number_iff_issued",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["party_id"], ["party.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["credits_document_pk"], ["sales_document.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "document_number", name="uq_sales_document_company_id"),
        sa.Index(CREDIT_INDEX, "credits_document_pk", unique=True),
    )


def upgrade() -> None:
    with op.batch_alter_table("sales_document", copy_from=_sales_document_table()) as batch:
        # sa.true() renders "true" on PostgreSQL and "1" on SQLite; a literal 1
        # would be rejected by the former as an integer.
        batch.add_column(sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("sales_document") as batch:
        batch.alter_column(COLUMN, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(
        "sales_document",
        copy_from=_sales_document_table(sa.Column(COLUMN, sa.Boolean(), nullable=False)),
    ) as batch:
        batch.drop_column(COLUMN)
