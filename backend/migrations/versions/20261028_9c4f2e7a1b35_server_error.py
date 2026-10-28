"""The server's failures, kept without anybody's data: one table, ``server_error``.

For ``myntos.domain.server_errors`` and the operator's page, ``/drift/``
(docs/operator.md). Every ERROR the backend logs is boiled down to the code
that failed: the exception's type and its causes' types, the traceback's frames,
the log line's template and the route's template. One row per kind of failure,
with a count and the first and last time it was seen.

What the table does **not** have is the design, as for ``feedback``: no
``user_id``, no ``company_id``, no IP address, no user agent, no exception
message and no logged value. No foreign key, so docs/gdpr.md's erasure has
nothing to do here.

**What it infers:** nothing. The table starts empty.

**What it refuses to guess:** the failures before it. They are in the journal
on the machine, with the client's address beside them and the message in
full, and boiling them down after the fact would mean parsing tracebacks out of
text and trusting the parse to have left every value out. The log starts with
the first failure after this revision.

Revision ID: 9c4f2e7a1b35
Revises: e4a9c2b7d15f
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c4f2e7a1b35"
down_revision: str | None = "e4a9c2b7d15f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "server_error",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("logger", sa.String(length=120), nullable=False),
        sa.Column("template", sa.String(length=300), nullable=False),
        sa.Column("route", sa.String(length=300), nullable=True),
        sa.Column("exception", sa.String(length=200), nullable=True),
        sa.Column("causes", json, nullable=False),
        sa.Column("frames", json, nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_server_error_fingerprint"),
    )
    op.create_index("ix_server_error_last_seen_at", "server_error", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_server_error_last_seen_at", table_name="server_error")
    op.drop_table("server_error")
