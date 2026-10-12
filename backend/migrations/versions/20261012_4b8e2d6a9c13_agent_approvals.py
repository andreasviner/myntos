"""Requests from an agent that wait for a person's approval.

Two changes, for docs/agents-and-api.md rule 13.

``agent_key.approval_scopes`` is what a key may only *ask* to do: a request
needing one of these is stored and waits for its person on /godkjenning/
instead of running.

``agent_approval`` and ``agent_approval_file`` are those stored requests, and
the files sent with them (``myntos.domain.identity.models.AgentApproval``).

**What it infers:** that every existing key does on its own exactly what it
did before, and asks for nothing, so ``approval_scopes`` is empty on every
row. That is not a guess: nothing could ask for approval before this
revision, and widening or narrowing what a key does without its person
deciding is the one thing a migration here must never do.

**What it refuses to guess:** nothing else. There is no older notion of a
waiting request to carry over.

Revision ID: 4b8e2d6a9c13
Revises: 9d2b6f4e1a37
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8e2d6a9c13"
down_revision: str | None = "9d2b6f4e1a37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The server default stays, unlike the switch in 9d2b6f4e1a37: the model
    # declares it too, because "asks for nothing" is the right answer for a
    # row written by anything that does not know about approval.
    op.add_column(
        "agent_key",
        sa.Column("approval_scopes", sa.String(length=64), nullable=False, server_default=""),
    )

    op.create_table(
        "agent_approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("query", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        sa.Column("form", sa.Text(), nullable=True),
        sa.Column("filing_type", sa.String(length=32), nullable=True),
        sa.Column("filing_year", sa.Integer(), nullable=True),
        sa.Column("filing_period", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("result_status", sa.Integer(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_agent_approval_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["agent_key.id"],
            name="fk_agent_approval_key_id_agent_key",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["app_user.id"],
            name="fk_agent_approval_decided_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_approval"),
    )
    op.create_index(
        "ix_agent_approval_company_id_status", "agent_approval", ["company_id", "status"]
    )
    op.create_index("ix_agent_approval_key_id", "agent_approval", ["key_id"])

    op.create_table(
        "agent_approval_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("attach", sa.Boolean(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["agent_approval.id"],
            name="fk_agent_approval_file_approval_id_agent_approval",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_approval_file"),
    )
    op.create_index("ix_agent_approval_file_approval_id", "agent_approval_file", ["approval_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_approval_file_approval_id", table_name="agent_approval_file")
    op.drop_table("agent_approval_file")
    op.drop_index("ix_agent_approval_key_id", table_name="agent_approval")
    op.drop_index("ix_agent_approval_company_id_status", table_name="agent_approval")
    op.drop_table("agent_approval")
    with op.batch_alter_table("agent_key") as batch:
        batch.drop_column("approval_scopes")
