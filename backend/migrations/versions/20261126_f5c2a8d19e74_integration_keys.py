"""Integration keys: a server's static secret, in the table agent keys live in.

The owner's decision of 2026-11-26 (docs/agents-and-api.md rule 15): a server
may hold a key that an owner made, bound to IP addresses and limited to
invoicing, which sends invoices without waiting for a person. Such a key is a
row in ``agent_key`` with ``kind`` ``integration``
(``myntos.domain.identity.models.AgentKeyKind``) rather than a table of its
own, because ``gl_transaction.created_via_agent_key_id`` and the other
"via key" columns point at ``agent_key``: a voucher an integration posts is
stamped with its key like any other, and the stops, the refusal counter and
the mails then need nothing new to find it.

Four new columns: ``kind``; ``secret_hash``, the digest of the ``myntos_ik_``
secret, unique; ``allowed_ips``, the addresses it may be used from; and
``purpose``, which routes it may call. Three columns an integration key has no
value for become nullable: ``connection_key_hash`` and
``connection_key_expires_at`` (it is never exchanged) and ``expires_at`` (it
has no yearly expiry). ``ck_agent_key_credential_matches_kind`` keeps every
row wholly one kind: an agent key still has all three, an integration key has
its secret, addresses and purpose and no connection key.

**What it infers:** that every existing row is an agent key, because nothing
could make anything else before this revision, so ``kind`` is ``agent`` on
every row and the three relaxed columns keep the values they have. The server
default stays, as ``approval_scopes``'s did in 4b8e2d6a9c13: "an agent key" is
the right answer for a row written by anything that does not know about
integration keys.

**What it refuses to guess:** an integration key for anybody. No existing key
becomes one, and none is made here: a key that sends invoices on its own is
made by an owner, on the page, with the addresses they choose.

Revision ID: f5c2a8d19e74
Revises: e2b7c4d91f53
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "f5c2a8d19e74"
down_revision: str | None = "e2b7c4d91f53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND_CHECK = (
    "(kind = 'agent' AND connection_key_hash IS NOT NULL"
    " AND connection_key_expires_at IS NOT NULL AND expires_at IS NOT NULL"
    " AND secret_hash IS NULL)"
    " OR (kind = 'integration' AND secret_hash IS NOT NULL"
    " AND allowed_ips IS NOT NULL AND purpose IS NOT NULL"
    " AND connection_key_hash IS NULL)"
)


def _agent_key(*extra: sa.Column[object], relaxed: bool = False) -> sa.Table:
    """``agent_key`` as ``4b8e2d6a9c13`` left it, plus ``extra``.

    Handed to batch mode, which rebuilds the table on SQLite (it cannot make a
    column nullable in place) and would otherwise reflect it without its check
    constraint. Columns in the order the revisions that added them ran.
    ``relaxed`` is the table as this revision leaves it, for the way back.
    """
    return sa.Table(
        "agent_key",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=64), nullable=False),
        sa.Column("connection_key_hash", sa.String(length=64), nullable=relaxed),
        sa.Column("connection_key_expires_at", sa.DateTime(timezone=True), nullable=relaxed),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=relaxed),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_client", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("approval_scopes", sa.String(length=64), nullable=False, server_default=""),
        *extra,
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


def _new_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="agent"),
        sa.Column("secret_hash", sa.String(length=64), nullable=True),
        sa.Column("allowed_ips", sa.Text(), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=True),
    ]


def upgrade() -> None:
    with op.batch_alter_table("agent_key", copy_from=_agent_key()) as batch:
        for column in _new_columns():
            batch.add_column(column)
        for name in ("connection_key_hash", "connection_key_expires_at", "expires_at"):
            batch.alter_column(name, nullable=True)
        batch.create_unique_constraint("uq_agent_key_secret_hash", ["secret_hash"])
        batch.create_check_constraint("credential_matches_kind", KIND_CHECK)


def downgrade() -> None:
    # An integration key has nothing to put in the columns that become NOT
    # NULL again, and nothing on the way back could use it: it goes. The
    # vouchers it stamped keep their content and lose only the stamp, which
    # every "via key" column drops with ON DELETE SET NULL.
    op.execute("DELETE FROM agent_key WHERE kind = 'integration'")
    # No drop_constraint for the two new constraints, for the reason
    # f3a5c81d90e4 gives: batch mode rebuilds from copy_from and never
    # reflects them, and dropping their columns takes them along anyway.
    with op.batch_alter_table(
        "agent_key", copy_from=_agent_key(*_new_columns(), relaxed=True)
    ) as batch:
        for name in ("connection_key_hash", "connection_key_expires_at", "expires_at"):
            batch.alter_column(name, nullable=False)
        for column in reversed(_new_columns()):
            batch.drop_column(column.name)
