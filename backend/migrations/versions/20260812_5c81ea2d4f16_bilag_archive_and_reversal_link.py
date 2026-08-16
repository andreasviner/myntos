"""Bilag document archive, and a link from a reversal to what it reverses.

Two additions, both driven by the cash-flow register.

``bilag_attachment`` holds the metadata for a stored document; the bytes live
in ``myntos.storage``. Nothing above this table deletes from it —
bokføringsloven § 13 requires primary documentation to stay available for five
years after the financial year ends.

``gl_transaction.reverses_transaction_pk`` is a self-reference from a reversing
voucher to the one it undoes. Without it a reversal is only recognisable by its
mirrored amounts, so nothing can tell an auditor which entry was corrected, and
nothing stops the same entry being reversed twice. The unique index is what
enforces "at most one reversal".

``batch_alter_table`` with an explicit ``copy_from`` again (see revision
3d7c15ba90e4): SQLite cannot add a foreign key in place, and supplying the
table's current shape rather than reflecting it is what keeps the recreated
table's constraint names identical to the ones the models declare.
``bilag_attachment`` is created *after* the rebuild so its foreign key is
attached to the final table.

Revision ID: 5c81ea2d4f16
Revises: 3d7c15ba90e4
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "5c81ea2d4f16"
down_revision: str | None = "3d7c15ba90e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVERSAL_FK = "fk_gl_transaction_reverses_transaction_pk_gl_transaction"
REVERSAL_INDEX = "uq_gl_transaction_reverses"


def _gl_transaction(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``gl_transaction``, with or without the new column.

    Written once and parameterised because batch mode needs the *before* shape
    on the way up and the *after* shape on the way down; keeping two hand-copied
    column lists in sync is exactly the kind of drift this migration exists to
    avoid.
    """
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
            # sa.func.now(), not sa.text("now()"): the literal is PostgreSQL
            # syntax and leaves a migrated SQLite database unable to insert a
            # voucher at all. func.now() renders now() on PostgreSQL and
            # CURRENT_TIMESTAMP on SQLite, and matches what the model declares.
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("voucher_type", sa.String(length=64), nullable=True),
        sa.Column("source_document_id", sa.String(length=128), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        *extra,
        sa.CheckConstraint("period BETWEEN 1 AND 12", name="period_is_a_month"),
        sa.ForeignKeyConstraint(
            ["journal_id"],
            ["journal.id"],
            name="fk_gl_transaction_journal_id_journal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction"),
        sa.UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
    )


def _reversal_column() -> sa.Column[object]:
    return sa.Column("reverses_transaction_pk", sa.Integer(), nullable=True)


def upgrade() -> None:
    with op.batch_alter_table("gl_transaction", copy_from=_gl_transaction()) as batch:
        batch.add_column(_reversal_column())
        batch.create_foreign_key(
            REVERSAL_FK,
            "gl_transaction",
            ["reverses_transaction_pk"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index(REVERSAL_INDEX, "gl_transaction", ["reverses_transaction_pk"], unique=True)

    op.create_table(
        "bilag_attachment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("byte_size > 0", name="byte_size_is_positive"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="CASCADE"),
        # SET NULL: erasing the person who uploaded a bilag must never erase
        # the bilag.
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )


def downgrade() -> None:
    op.drop_table("bilag_attachment")
    op.drop_index(REVERSAL_INDEX, table_name="gl_transaction")
    with op.batch_alter_table(
        "gl_transaction", copy_from=_gl_transaction(_reversal_column())
    ) as batch:
        batch.drop_constraint(REVERSAL_FK, type_="foreignkey")
        batch.drop_column("reverses_transaction_pk")
