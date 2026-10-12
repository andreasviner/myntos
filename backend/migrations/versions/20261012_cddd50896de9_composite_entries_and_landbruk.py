"""Composite register entries, and the farming add-on switch.

Two columns.

``gl_transaction_line.entry_type`` is the register category a trade line was
booked as. A composite entry (a settlement from Tine: milk sold, a levy
deducted, feed set off, one bank line) has several categories behind one
voucher, and ``voucher_type`` can hold only one.

``company.landbruk`` is whether the books get the farming add-on
(docs/landbruk.md): NULL means nobody has said and the næringskode decides,
True and False are a person's answer.

**What it infers:** nothing. Every existing company gets NULL, which is the
honest state of a switch nobody has touched: a company registered under
næringskode 01 or 02 will see the farm categories from now on, and every other
company sees exactly what it saw before. No posted line is touched.

**What it refuses to guess:** the category of a line written before this
revision. It could be read off its voucher's ``voucher_type`` for an ordinary
entry, but writing that onto posted lines would be a migration editing posted
vouchers, and nothing reads the column on an ordinary entry anyway: the
register reads ``voucher_type`` there, as it always has.

Revision ID: cddd50896de9
Revises: 4b8e2d6a9c13
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cddd50896de9"
down_revision: str | None = "4b8e2d6a9c13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gl_transaction_line", sa.Column("entry_type", sa.String(length=64), nullable=True)
    )
    op.add_column("company", sa.Column("landbruk", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "landbruk")
    op.drop_column("gl_transaction_line", "entry_type")
