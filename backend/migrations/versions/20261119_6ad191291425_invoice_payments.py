"""A customer paying an invoice: ``gl_transaction_line.settles_document_pk``.

For ``myntos.domain.faktura.payments``. A payment of an issued invoice is a
voucher of its own (the bank or cash account debited, 1500 credited with the
customer), and its 1500 line names the invoice it pays. What is paid of an
invoice is then the credits on 1500 that name it less the debits, read off the
ledger; a reversal copies the line, link included, so a payment undone nets to
nothing against its invoice without a second record that could disagree.

RESTRICT, like the invoice's own link to its voucher: nothing in the application
deletes an issued invoice, and nothing should be able to delete one out from
under a payment. The purge of a deleted company's books clears these links
before it deletes the invoices (``myntos.domain.identity.closure``).

**What it infers:** nothing. Every existing line gets NULL.

**What it refuses to guess:** which invoices are already paid. Before this
revision a payment could only be booked as something else: as a sale in the
register (which counted the revenue twice), as a manual voucher crediting 1500,
or in the previous program's books. Matching those to invoices by amount, date
or KID would mark the wrong invoice paid whenever two invoices to the same
customer are for the same amount, which is common, and a payment booked as a
sale is not a payment of anything in the ledger's eyes. So every issued invoice
starts unpaid, and the person registers what has come in; where it is already
in the books as something else, that entry is reversed first, which the help
article says.

On SQLite the table is rebuilt, since SQLite cannot add a foreign key in place,
from the shape given here rather than reflected, for the constraint-name
reason ``3d7c15ba90e4`` gives. On PostgreSQL batch mode alters in place.

Revision ID: 6ad191291425
Revises: 8d7b599c0754
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "6ad191291425"
down_revision: str | None = "8d7b599c0754"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "settles_document_pk"
SETTLES_FK = "fk_gl_transaction_line_settles_document_pk_sales_document"
SETTLES_INDEX = "ix_gl_transaction_line_settles_document_pk"

MONEY = sa.Numeric(38, 2)


def _gl_transaction_line(*extra: sa.Column[object]) -> sa.Table:
    """``gl_transaction_line`` as ``cddd50896de9`` left it, plus ``extra``."""
    return sa.Table(
        "gl_transaction_line",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("debit", MONEY, nullable=False),
        sa.Column("credit", MONEY, nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("currency_amount", MONEY, nullable=True),
        sa.Column("exchange_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("tax_code_id", sa.Integer(), nullable=True),
        sa.Column("tax_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("tax_base", MONEY, nullable=True),
        sa.Column("debit_tax_amount", MONEY, nullable=True),
        sa.Column("credit_tax_amount", MONEY, nullable=True),
        sa.Column("vat_specification", sa.String(length=64), nullable=True),
        sa.Column("entry_type", sa.String(length=64), nullable=True),
        *extra,
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction_line"),
        sa.CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_are_non_negative"),
        sa.CheckConstraint("NOT (debit > 0 AND credit > 0)", name="one_side_only"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_gl_transaction_line_account_id_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["party.id"],
            name="fk_gl_transaction_line_party_id_party",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_code_id"],
            ["tax_code.id"],
            name="fk_gl_transaction_line_tax_code_id_tax_code",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_pk"],
            ["gl_transaction.id"],
            name="fk_gl_transaction_line_transaction_pk_gl_transaction",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "transaction_pk", "record_id", name="uq_gl_transaction_line_transaction_pk"
        ),
    )


def _settles_column() -> sa.Column[object]:
    return sa.Column(COLUMN, sa.Integer(), nullable=True)


def upgrade() -> None:
    with op.batch_alter_table("gl_transaction_line", copy_from=_gl_transaction_line()) as batch:
        batch.add_column(_settles_column())
        batch.create_foreign_key(
            SETTLES_FK, "sales_document", [COLUMN], ["id"], ondelete="RESTRICT"
        )
    # What an invoice has been paid is a sum over the lines that name it, asked
    # for every invoice on the list.
    op.create_index(SETTLES_INDEX, "gl_transaction_line", [COLUMN])


def downgrade() -> None:
    op.drop_index(SETTLES_INDEX, table_name="gl_transaction_line")
    # No drop_constraint for the foreign key, for the reason f8b3d27a4c19
    # gives: batch mode rebuilds from copy_from and never reflects it, and
    # dropping the column takes the constraint with it anyway.
    with op.batch_alter_table(
        "gl_transaction_line", copy_from=_gl_transaction_line(_settles_column())
    ) as batch:
        batch.drop_column(COLUMN)
