"""Record which person entered each voucher.

``gl_transaction`` said when the system recorded an entry
(``system_entry_date``) and never who recorded it. That is a gap only on books
with more than one person on them, and those are the ordinary case now that
access is a row per person per company (``d1a7f2c6b483``): a regnskapsfører and
the owner post into the same journal, and "who booked this as a private
withdrawal" had no answer anywhere in the data.

Nothing is backfilled, and the column stays nullable afterwards. The existing
rows genuinely have no recorded author — the information was never captured, so
any value written here would be a guess. Attributing them to the company's
first member would be the obvious guess and the wrong one: on a set of books
where a bookkeeper does the entering, it would name the owner for every entry
they never made. The register says «ukjent» instead, which is true.

The column is also nullable permanently, for two reasons that outlive the
backfill: GDPR erasure removes the person and must leave the voucher (hence
``ondelete="SET NULL"`` rather than CASCADE — a posted entry is history and
bokføringsloven § 13 keeps it for five years), and a voucher posted by a future
scheduled job has no person behind it.

Not a SAF-T field. The standard's ``Transaction`` has no user element, so this
is ours and the exporter does not read it.

Revision ID: f8b3d27a4c19
Revises: e7a1c93f6b28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "f8b3d27a4c19"
down_revision: str | None = "e7a1c93f6b28"
branch_labels: None = None
depends_on: None = None

COLUMN = "created_by_user_id"
AUTHOR_FK = "fk_gl_transaction_created_by_user_id_app_user"

# SQLite cannot add a foreign key in place, so alembic rebuilds the table and
# has to be told what it looked like first: ``gl_transaction`` as of the
# previous revision, which is the shape ``5c81ea2d4f16`` left plus the reversal
# link and the correction link ``f3a5c81d90e4`` added. The column order matters
# — batch mode copies the rows positionally — so the two links stay last, in the
# order the revisions that added them ran.


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
        sa.Column("corrects_transaction_pk", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["corrects_transaction_pk"],
            ["gl_transaction.id"],
            name="fk_gl_transaction_corrects_transaction_pk_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction"),
        sa.UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
        sa.Index("uq_gl_transaction_reverses", "reverses_transaction_pk", unique=True),
        sa.Index("ix_gl_transaction_corrects", "corrects_transaction_pk"),
    )


def _author_column() -> sa.Column[object]:
    return sa.Column(COLUMN, sa.Integer(), nullable=True)


def upgrade() -> None:
    with op.batch_alter_table("gl_transaction", copy_from=_gl_transaction()) as batch:
        batch.add_column(_author_column())
        batch.create_foreign_key(
            AUTHOR_FK,
            "app_user",
            [COLUMN],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # No ``drop_constraint`` for the foreign key, for the reason
    # ``f3a5c81d90e4`` gives: batch mode rebuilds from ``copy_from`` and never
    # reflects the constraint, so a drop it was not handed fails with "No such
    # constraint" — a downgrade that only breaks when somebody needs it.
    # Dropping the column takes the constraint with it anyway.
    with op.batch_alter_table(
        "gl_transaction",
        copy_from=_gl_transaction(_author_column()),
    ) as batch:
        batch.drop_column(COLUMN)
