"""Remember who an account belongs to, and who an invitation was addressed to.

Two changes, one purpose: joining the people in Enhetsregisteret to the people
who can open the books.

``user_government_identity`` is the durable half of an ID-porten login.
``government_login_attempt`` already holds a ``pid``, but that row is an
authorization - minutes old, single-use, worthless once its token expires - and
the question "which person is behind this account" has to outlive it. Storing
it there would mean the link vanishing an hour after it was made, which is
exactly when it starts being useful. One row per account, replaced rather than
appended to; deleted with the account, because the reason for holding a
fodselsnummer ends when the account does.

``organization_invitation.full_name`` is who the inviter said they were
inviting. A label and never a check - acceptance still requires signing in as
the invited address - but it is what lets the invitation email greet a person
and the register's role list show that a board member has been asked.

Both columns are nullable and nothing backfills them. An account that has never
completed an ID-porten login has no person to name, and an invitation sent
before today was addressed to an address and nothing more; inventing either
would be inventing data.

Revision ID: b2f9c41d70a8
Revises: a4d6f1b83c07
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2f9c41d70a8"
down_revision: str | None = "a4d6f1b83c07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_government_identity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pid", sa.String(length=11), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("acr", sa.String(length=64), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_user_government_identity_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_government_identity"),
        # Unique on the account, not on the pid: one person legitimately holds
        # two accounts (their own and one at a client), and a unique pid would
        # turn the second login into a database error.
        sa.UniqueConstraint("user_id", name="uq_user_government_identity_user_id"),
    )
    op.add_column(
        "organization_invitation",
        sa.Column("full_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_invitation", "full_name")
    op.drop_table("user_government_identity")
