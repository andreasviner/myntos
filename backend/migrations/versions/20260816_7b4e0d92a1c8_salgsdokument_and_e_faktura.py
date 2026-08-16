"""Salgsdokument (faktura/kreditnota), its lines, and its number series.

Three new tables plus four columns on ``company``.

Revision note: ``sales_document`` also snapshots ``seller_organisation_form``
alongside the Foretaksregisteret flag, so the marking can be checked against what
Enhetsregisteret says rather than trusted from a checkbox.

``sales_document`` is the invoice as bokføringsforskriften § 5-1 defines it. Two
constraints in it are load-bearing rather than defensive:

- ``number_iff_issued`` ties the document number, the issue timestamp and the
  status together. § 5-1-3 wants a numbering that can be checked for
  completeness, and the way that quietly stops being true is a draft that
  acquired a number and was then discarded. The database refuses the combination
  outright rather than trusting every code path that writes a status.
- ``uq_sales_document_credits`` allows at most one kreditnota per faktura, the
  same shape as ``uq_gl_transaction_reverses`` on the ledger. An index rather
  than a unique constraint for the same reason as there: SQLite can add an index
  to an existing table but rebuilds it for a constraint.

``sales_document_series`` is a per-company counter. The unique constraint on
``(company_id, document_number)`` above is what makes a concurrent double-issue a
failed request instead of two invoices sharing a number.

The four ``company`` columns are what a salgsdokument has to be able to state
about the seller: whether "MVA" follows the orgnr, whether "Foretaksregisteret"
must appear, which organisasjonsform decides that, and where to pay. They are
added with ``server_default`` so existing rows get a value, and the default is
then dropped — the model does not declare one, and leaving it in the database
would make every future autogenerate want to remove it.

``batch_alter_table`` with an explicit ``copy_from`` (see revisions 3d7c15ba90e4
and 5c81ea2d4f16): SQLite rebuilds the table, and supplying its current shape
rather than reflecting it keeps the recreated constraint names identical to the
ones the models declare.

Revision ID: 7b4e0d92a1c8
Revises: 5c81ea2d4f16
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "7b4e0d92a1c8"
down_revision: str | None = "5c81ea2d4f16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREDIT_INDEX = "uq_sales_document_credits"

MONEY = sa.Numeric(38, 2)
RATE = sa.Numeric(9, 6)
PRICE = sa.Numeric(38, 4)
QUANTITY = sa.Numeric(24, 6)

# Kept in one place so upgrade() and downgrade() cannot disagree about which
# columns this revision owns. The third element is the server default used only
# to backfill existing rows; sa.false() renders "false" on PostgreSQL and "0" on
# SQLite, where a literal 0 would be rejected by the former as an integer.
_COMPANY_COLUMNS = (
    ("vat_registered", sa.Boolean(), sa.false()),
    ("in_foretaksregisteret", sa.Boolean(), sa.false()),
    ("organisation_form", sa.String(length=16), None),
    ("bank_account", sa.String(length=34), None),
)


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` before this revision, plus anything passed in."""
    return sa.Table(
        "company",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        *extra,
        sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_company_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        for name, type_, default in _COMPANY_COLUMNS:
            batch.add_column(
                sa.Column(name, type_, nullable=default is None, server_default=default)
            )
    # Dropped now that every existing row has a value: the model declares a
    # Python-side default only, and a lingering server default would show up as
    # a phantom diff on every autogenerate from here on.
    with op.batch_alter_table("company") as batch:
        for name, _type, default in _COMPANY_COLUMNS:
            if default is not None:
                batch.alter_column(name, server_default=None)

    op.create_table(
        "sales_document",
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
        # No constraint ties due_date to issue_date: a forfallsdato in the past
        # is unusual but lawful, and it is exactly what invoicing late with terms
        # counted from delivery produces. See the note in
        # myntos.domain.faktura.models.
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        # RESTRICT, not CASCADE: a party or a voucher must not be removable out
        # from under an issued sales document.
        sa.ForeignKeyConstraint(["party_id"], ["party.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["credits_document_pk"], ["sales_document.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "document_number", name="uq_sales_document_company_id"),
    )
    op.create_index(CREDIT_INDEX, "sales_document", ["credits_document_pk"], unique=True)

    op.create_table(
        "sales_document_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_pk", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit_code", sa.String(length=8), nullable=False),
        sa.Column("unit_price", PRICE, nullable=False),
        sa.Column("discount_rate", RATE, nullable=False),
        sa.Column("tax_code", sa.String(length=32), nullable=False),
        sa.Column("tax_rate", RATE, nullable=False),
        sa.Column("income_account_number", sa.String(length=32), nullable=False),
        sa.Column("net_amount", MONEY, nullable=False),
        sa.Column("vat_amount", MONEY, nullable=False),
        sa.CheckConstraint("net_amount >= 0 AND vat_amount >= 0", name="amounts_are_non_negative"),
        sa.CheckConstraint("quantity <> 0", name="quantity_is_not_zero"),
        sa.ForeignKeyConstraint(["document_pk"], ["sales_document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_pk", "line_no", name="uq_sales_document_line_document_pk"),
    )

    op.create_table(
        "sales_document_series",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("next_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("next_number > 0", name="next_number_is_positive"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_sales_document_series_company_id"),
    )


def downgrade() -> None:
    op.drop_table("sales_document_series")
    op.drop_table("sales_document_line")
    op.drop_index(CREDIT_INDEX, table_name="sales_document")
    op.drop_table("sales_document")

    added = [
        sa.Column(name, type_, nullable=default is None)
        for name, type_, default in _COMPANY_COLUMNS
    ]
    with op.batch_alter_table("company", copy_from=_company_table(*added)) as batch:
        for name, _type, _default in _COMPANY_COLUMNS:
            batch.drop_column(name)
