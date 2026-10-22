"""Feedback from anybody, kept without a name: one table, ``feedback``.

For ``myntos.domain.feedback`` and the four public routes under
``/api/v1/tilbakemeldinger`` (docs/feedback.md). A person or an agent sends a
bug report, an improvement or a wish; the row holds what they wrote, what kind
it is, where it happened, whether they say they are a person or an agent, and
the operator's status, reply and public summary.

What the table does **not** have is the design: no ``user_id``, no
``company_id``, no IP address, no user agent. Nothing points at ``app_user``,
so docs/gdpr.md's erasure has nothing to do here; the sender withdraws what
they wrote with the reference they were given, whose SHA-256 digest is the
only thing that ties a row to anybody.

**What it infers:** nothing. The table starts empty.

**What it refuses to guess:** who sent anything. There is no earlier feedback
to bring in: until now it came by e-mail to kontakt@myntos.no, with the
sender's address on it, and copying any of that here would put names in a
table that promises none.

Revision ID: a84dfde214d1
Revises: f4a9d2c63b18
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a84dfde214d1"
down_revision: str | None = "f4a9d2c63b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("agent_name", sa.String(length=80), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("summary", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_hash", name="uq_feedback_reference_hash"),
    )
    op.create_index("ix_feedback_created_at", "feedback", ["created_at"])
    op.create_index("ix_feedback_status", "feedback", ["status"])


def downgrade() -> None:
    op.drop_index("ix_feedback_status", table_name="feedback")
    op.drop_index("ix_feedback_created_at", table_name="feedback")
    op.drop_table("feedback")
