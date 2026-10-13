"""Manual vouchers: the written reason a voucher carries.

One table, ``voucher_explanation``: the text that documents a voucher no outside
paper documents, one per voucher, append-only (bokføringsforskriften § 5-12, and
§ 6-4 for an estimate). The first writer is the manual voucher
(``myntos.domain.manuelt_bilag``), posted in its own journal ``MANUELT``, which
needs no schema of its own: a journal row is made the first time a company posts
one, as the other journals are.

**What it infers:** nothing. No voucher posted before this revision has an
explanation, and none is given one: the register's payments are documented by
their bilag, the opening balance by the previous year's accounts, the closing by
its own log.

**What it refuses to guess:** an explanation for an existing voucher, from its
``description`` or anything else. Writing documentation onto a voucher after
the fact, and dating it as though it came with the voucher, is exactly what
bokføringsloven § 10 forbids.

Revision ID: e4b7c2a9d815
Revises: cddd50896de9
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c2a9d815"
down_revision: str | None = "cddd50896de9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voucher_explanation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_voucher_explanation_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_pk"],
            ["gl_transaction.id"],
            name="fk_voucher_explanation_transaction_pk_gl_transaction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_voucher_explanation"),
        sa.UniqueConstraint("transaction_pk", name="uq_voucher_explanation_transaction_pk"),
    )


def downgrade() -> None:
    op.drop_table("voucher_explanation")
