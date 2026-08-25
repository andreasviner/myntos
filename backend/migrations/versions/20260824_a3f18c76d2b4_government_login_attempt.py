"""Store an ID-porten login and the token it produces, encrypted.

Skatteetaten requires a person to review a melding before it is filed, so the
token authorizing the filing has to outlive the callback that produced it. The
backend sleeps between requests, so that means a row rather than memory - the
same reasoning ``oauth_login_attempt`` records for its own short-lived state.

Deliberately a second table rather than a column on ``oauth_login_attempt``: one
signs a person into Myntos, the other proves to Skatteetaten who they are, and a
single table would let either callback complete the other's flow.

``sealed_token`` is Fernet ciphertext (``myntos.auth.idporten.vault``), never a
readable credential. ``pid`` is 11 characters - a fodselsnummer - and is here so
the UI can show who is about to file; it is not an identifier for anything else,
and rows are minutes old by design.

Revision ID: a3f18c76d2b4
Revises: e2b70c5a4d19
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f18c76d2b4"
down_revision: str | None = "e2b70c5a4d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "government_login_attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("redirect_to", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sealed_token", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pid", sa.String(length=11), nullable=True),
        sa.Column("acr", sa.String(length=64), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_government_login_attempt_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_government_login_attempt"),
        sa.UniqueConstraint("state", name="uq_government_login_attempt_state"),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )


def downgrade() -> None:
    op.drop_table("government_login_attempt")
