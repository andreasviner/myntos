"""Keys for agents and integrations: the company switch, the keys, their tokens.

Three changes, for docs/agents-and-api.md rules 3, 4 and 6.

``company.agent_access_enabled`` is the switch an owner or admin turns on
before any key can be made for those books.

``agent_key`` and ``agent_token`` are the keys and the rotating chain each one
is exchanged into (``myntos.domain.identity.models.AgentKey``).

``gl_transaction.created_via_agent_key_id`` records the key a voucher was
registered through, beside the person who registered it.

**What it infers:** that no company has agreed to keys yet, so the switch is
backfilled **off** for every existing row. That is not a guess: nothing could
turn it on before this revision, and on is a decision about where a company's
books may be read from that only its own people can make. And that no existing
voucher was registered through a key, which is true for the same reason, so
the new column is NULL on every row and means exactly "not through a key".

**What it refuses to guess:** nothing else. There is no older notion of a key
to carry over.

Revision ID: 9d2b6f4e1a37
Revises: 7a3e5c1d9b24
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "9d2b6f4e1a37"
down_revision: str | None = "7a3e5c1d9b24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SWITCH = "agent_access_enabled"
VIA = "created_via_agent_key_id"
VIA_FK = "fk_gl_transaction_created_via_agent_key_id_agent_key"


def _gl_transaction(*extra: sa.Column[object]) -> sa.Table:
    """``gl_transaction`` as ``f8b3d27a4c19`` left it, plus ``extra``.

    Handed to batch mode, which rebuilds the table on SQLite (it cannot add a
    foreign key in place) and copies the rows positionally, so the columns are
    in the order the revisions that added them ran: the author last but one,
    and the key after it.
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
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("voucher_type", sa.String(length=64), nullable=True),
        sa.Column("source_document_id", sa.String(length=128), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverses_transaction_pk", sa.Integer(), nullable=True),
        sa.Column("corrects_transaction_pk", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_gl_transaction_created_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction"),
        sa.UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
        sa.Index("uq_gl_transaction_reverses", "reverses_transaction_pk", unique=True),
        sa.Index("ix_gl_transaction_corrects", "corrects_transaction_pk"),
    )


def _via_column() -> sa.Column[object]:
    return sa.Column(VIA, sa.Integer(), nullable=True)


def upgrade() -> None:
    # Backfilled off through a server default that is dropped again at once:
    # the model declares the default Python-side, and one left in the database
    # is a phantom diff on every autogenerate (b7c2e94f10a3 does the same).
    op.add_column(
        "company",
        sa.Column(SWITCH, sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("company") as batch:
        batch.alter_column(SWITCH, server_default=None)

    op.create_table(
        "agent_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=64), nullable=False),
        sa.Column("connection_key_hash", sa.String(length=64), nullable=False),
        sa.Column("connection_key_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_client", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_agent_key_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_agent_key_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_key"),
        sa.UniqueConstraint("connection_key_hash", name="uq_agent_key_connection_key_hash"),
    )

    op.create_table(
        "agent_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["agent_key.id"],
            name="fk_agent_token_key_id_agent_key",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_token"),
        sa.UniqueConstraint("token_hash", name="uq_agent_token_token_hash"),
    )
    op.create_index("ix_agent_token_key_id", "agent_token", ["key_id"])

    with op.batch_alter_table("gl_transaction", copy_from=_gl_transaction()) as batch:
        batch.add_column(_via_column())
        batch.create_foreign_key(VIA_FK, "agent_key", [VIA], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    # No drop_constraint for the foreign key, for the reason f3a5c81d90e4
    # gives: batch mode rebuilds from copy_from and never reflects it, and
    # dropping the column takes the constraint with it anyway.
    with op.batch_alter_table("gl_transaction", copy_from=_gl_transaction(_via_column())) as batch:
        batch.drop_column(VIA)
    op.drop_index("ix_agent_token_key_id", table_name="agent_token")
    op.drop_table("agent_token")
    op.drop_table("agent_key")
    with op.batch_alter_table("company") as batch:
        batch.drop_column(SWITCH)
