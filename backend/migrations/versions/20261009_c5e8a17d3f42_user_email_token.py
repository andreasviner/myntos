"""Single-use links mailed to an account's own address: confirmation and reset.

One table for both kinds, keyed by purpose, shaped like
``organization_invitation``: only a SHA-256 digest of each token is stored, with
a deadline and a stamp when it is spent or superseded. See
``myntos.domain.identity.models.UserEmailToken``.

**What it infers:** nothing about existing accounts. ``app_user`` is not
touched. ``email_verified_at`` already exists (it came with the identity
tables) and already says the truthful thing for every row: stamped where a
provider vouched for the address, NULL for every password signup, because no
confirmation mail was ever sent until now.

**What it refuses to guess:** that an existing password account owns its
address. Backfilling ``email_verified_at`` for them would write down a check
that never happened, in the one column whose whole meaning is that it did. They
stay unverified, and that locks nobody out: an unverified account signs in,
keeps books, invites and accepts invitations exactly as before. What it does
not get is its address used as the Reply-To on mail Myntos sends to somebody
else, and a notice on the front page offering the confirmation link. See
docs/ARCHITECTURE.md, "Credentials and sessions".

Revision ID: c5e8a17d3f42
Revises: c3e8a5f1d207
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e8a17d3f42"
down_revision: str | None = "c3e8a5f1d207"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_email_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "verify_email",
                "reset_password",
                name="email_token_purpose",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_user_email_token_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_email_token"),
        sa.UniqueConstraint("token_hash", name="uq_user_email_token_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )
    op.create_index(
        "ix_user_email_token_user_purpose",
        "user_email_token",
        ["user_id", "purpose", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_email_token_user_purpose", table_name="user_email_token")
    op.drop_table("user_email_token")
