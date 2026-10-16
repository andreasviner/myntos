"""The farm's year-end count of livestock and stock, and its list of private uttak.

Four tables for ``myntos.domain.arsoppgjor.buskap`` and ``.uttak``
(docs/landbruk.md):

``farm_count`` / ``farm_count_line``
    One posted count per row, with its opening and closing lines (kind,
    category, quantity, valuation, unit value, value, account) and the
    ``TELLING`` voucher that brought 1450, 1400 and 1490 to the counted values.
``farm_withdrawal`` / ``farm_withdrawal_line``
    One posted list of private uttak per row, with each product's quantity,
    norm or omsetningsverdi, VAT code and VAT, and its ``UTTAK`` voucher.

Written once and never updated: a correction or a withdrawal is a new row that
names the one it replaces (``corrects_id``), as every correction in Myntos is.

**What it infers:** nothing. The tables start empty.

**What it refuses to guess:** a count from the balance on 1450, or an uttak
list from the postings on 2060. A balance does not say how many cows stand
behind it or at which values, and the næringsspesifikasjon and the average rule
(takseringsreglene § 5-2) both need exactly that. A farm that brought its
herd's value in through the opening balance types its opening rows the first
year; no posted voucher is touched.

Revision ID: d2f6a8c41e57
Revises: c7a4e2f91b38
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f6a8c41e57"
down_revision: str | None = "c7a4e2f91b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 2)
RATE = sa.Numeric(9, 6)
QUANTITY = sa.Numeric(18, 3)
UNIT_VALUE = sa.Numeric(18, 4)


def upgrade() -> None:
    op.create_table(
        "farm_count",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("counted_on", sa.Date(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("corrects_id", sa.Integer(), nullable=True),
        sa.Column("opening_source", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["corrects_id"], ["farm_count.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_farm_count_company", "farm_count", ["company_id", "financial_year"])
    op.create_table(
        "farm_count_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("count_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("valuation", sa.String(length=16), nullable=False),
        sa.Column("unit_value", UNIT_VALUE, nullable=False),
        sa.Column("value", MONEY, nullable=False),
        sa.Column("account_number", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["count_id"], ["farm_count.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "farm_withdrawal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("corrects_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["corrects_id"], ["farm_withdrawal.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_farm_withdrawal_company", "farm_withdrawal", ["company_id", "financial_year"]
    )
    op.create_table(
        "farm_withdrawal_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("withdrawal_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("product", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("unit_value", UNIT_VALUE, nullable=False),
        sa.Column("value", MONEY, nullable=False),
        sa.Column("tax_code", sa.String(length=8), nullable=True),
        sa.Column("vat_rate", RATE, nullable=True),
        sa.Column("vat", MONEY, nullable=False),
        sa.Column("account_number", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["withdrawal_id"], ["farm_withdrawal.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("farm_withdrawal_line")
    op.drop_index("ix_farm_withdrawal_company", table_name="farm_withdrawal")
    op.drop_table("farm_withdrawal")
    op.drop_table("farm_count_line")
    op.drop_index("ix_farm_count_company", table_name="farm_count")
    op.drop_table("farm_count")
