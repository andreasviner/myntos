"""Join a corrected voucher to the entry it replaced.

Correcting a posted entry produces three vouchers: the original, the reversal
that cancels it, and the replacement. ``reverses_transaction_pk`` joins the
first two and stops there, which left the third indistinguishable from an
unrelated entry posted the same day. The register showed all three as separate
payments, and neither a reader nor an auditor could tell that two of them were
one correction.

The alternative was to pair them by date and amount. A guess that is usually
right is worse than no answer here: two genuine payments to the same supplier
on the same day would be folded into a correction that never happened, and the
register would be lying about the books rather than merely being cluttered.

No unique index, unlike the reversal link. A replacement can itself be
corrected later, and each link in that chain points at the entry before it;
enforcing uniqueness would forbid nothing that can happen and would break the
second correction of the same entry if it ever could.

``ondelete="SET NULL"`` and not CASCADE: the replacement is the voucher that is
still true. Losing the entry it replaced must not take it along.

Revision ID: f3a5c81d90e4
Revises: d4e1a83b62c7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "f3a5c81d90e4"
down_revision: str | None = "d4e1a83b62c7"
branch_labels: None = None
depends_on: None = None

COLUMN = "corrects_transaction_pk"
CORRECTION_FK = "fk_gl_transaction_corrects_transaction_pk_gl_transaction"

# SQLite cannot add a foreign key in place, so alembic rebuilds the table and
# has to be told what it looked like first: ``gl_transaction`` as of the
# previous revision, which is the shape ``5c81ea2d4f16`` left plus its reversal
# link and index.


def _gl_transaction(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``gl_transaction``, with or without the new column."""
    return sa.Table(
        "gl_transaction",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("gl_posting_date", sa.Date(), nullable=False),
        sa.Column(
            "system_entry_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("voucher_type", sa.String(length=64), nullable=True),
        sa.Column("source_document_id", sa.String(length=128), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverses_transaction_pk", sa.Integer(), nullable=True),
        *extra,
        sa.CheckConstraint("period BETWEEN 1 AND 12", name="period_is_a_month"),
        sa.ForeignKeyConstraint(
            ["journal_id"],
            ["journal.id"],
            name="fk_gl_transaction_journal_id_journal",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_pk"],
            ["gl_transaction.id"],
            name="fk_gl_transaction_reverses_transaction_pk_gl_transaction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction"),
        sa.UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
        sa.Index("uq_gl_transaction_reverses", "reverses_transaction_pk", unique=True),
    )


def _correction_column() -> sa.Column[object]:
    return sa.Column(COLUMN, sa.Integer(), nullable=True)


def upgrade() -> None:
    with op.batch_alter_table("gl_transaction", copy_from=_gl_transaction()) as batch:
        batch.add_column(_correction_column())
        batch.create_foreign_key(
            CORRECTION_FK,
            "gl_transaction",
            [COLUMN],
            ["id"],
            ondelete="SET NULL",
        )
    # Every listing reads the register newest-first and then asks which of those
    # vouchers correct something, so the lookup is by this column and not by id.
    op.create_index("ix_gl_transaction_corrects", "gl_transaction", [COLUMN])


def downgrade() -> None:
    op.drop_index("ix_gl_transaction_corrects", table_name="gl_transaction")
    # No ``drop_constraint`` for the foreign key. Batch mode rebuilds the table
    # from ``copy_from`` and never reflects it, so the constraint alembic is
    # asked to drop has to be present in the shape passed in — and this one is
    # not, deliberately: dropping the column already leaves the table without
    # it. Asking for the drop anyway fails with "No such constraint", which is
    # a downgrade that only breaks when someone needs it.
    with op.batch_alter_table(
        "gl_transaction",
        copy_from=_gl_transaction(_correction_column()),
    ) as batch:
        batch.drop_column(COLUMN)
