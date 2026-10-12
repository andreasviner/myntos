"""BankID sign-in: what a federated flow was started for, and signups held for an address.

Two changes, one of which is a refusal.

``oauth_login_attempt`` gains ``intent`` and ``user_id``. Until now every
attempt was a sign-in, and a sign-in needs to know nothing else. BankID adds
two flows that do: a signup, where an identity linked to nothing is held until
the person types an email address (BankID has none to give), and a link from
/konto/, which must finish on the same account that started it. See
``myntos.domain.identity.models.OAuthIntent``.

``pending_provider_signup`` is that hold: the broker's ``sub``, the name BankID
asserted, a token digest and a deadline. Rows are deleted when used and swept
when expired, by the application, so nothing here needs a job.

**What it infers:** that every existing attempt was a sign-in. That is not a
guess, because nothing else could start one before this revision, so
``intent`` is backfilled ``login`` through its server default. ``user_id``
stays NULL on them, which is what a sign-in has.

**What it refuses to guess:** what to do with a ``vipps`` identity. Vipps Login
is removed in the same change, and the provider column is a VARCHAR with no
CHECK (``native_enum=False`` creates none), so the database itself would accept
the value forever; it is the ORM that could no longer read it, and the first
``/auth/me`` of such an account would fail. Production never configured Vipps,
so there should be none. If there is one, this stops rather than deleting it:
the row is the only way into somebody's books, and only a person can decide
whether that account gets a password or another provider first.

Revision ID: 7a3e5c1d9b24
Revises: c5a2e7d94b13
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "7a3e5c1d9b24"
down_revision: str | None = "c5a2e7d94b13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_FK = "fk_oauth_login_attempt_user_id_app_user"


def _oauth_login_attempt(*extra: sa.Column[object]) -> sa.Table:
    """``oauth_login_attempt`` as ``3d7c15ba90e4`` made it, plus ``extra``.

    Handed to batch mode, which rebuilds the table on SQLite (it cannot add a
    foreign key in place) and has to be told the shape rather than reflect it,
    for the constraint-name reason ``3d7c15ba90e4`` gives. The provider column
    is a plain VARCHAR(32) here, which is what the non-native enum always was.
    """
    return sa.Table(
        "oauth_login_attempt",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("redirect_to", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *extra,
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_login_attempt"),
        sa.UniqueConstraint("state", name="uq_oauth_login_attempt_state"),
    )


def _new_columns() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "intent",
            sa.String(length=16),
            nullable=False,
            server_default="login",
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
    )


def _refuse_vipps_rows() -> None:
    connection = op.get_bind()
    for table in ("user_identity", "oauth_login_attempt"):
        # The table names are the literals above, never input.
        count = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE provider = 'vipps'")  # noqa: S608
        ).scalar_one()
        if count:
            raise RuntimeError(
                f"{table} holds {count} row(s) with provider 'vipps', which this version "
                "can no longer read. Vipps Login is gone; give each such account a "
                "password or another sign-in, delete the rows by hand, and run the "
                "migration again. Nothing was changed."
            )


def upgrade() -> None:
    _refuse_vipps_rows()

    with op.batch_alter_table("oauth_login_attempt", copy_from=_oauth_login_attempt()) as batch:
        for column in _new_columns():
            batch.add_column(column)
        batch.create_foreign_key(OWNER_FK, "app_user", ["user_id"], ["id"], ondelete="CASCADE")

    op.create_table(
        "pending_provider_signup",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_to", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_pending_provider_signup"),
        sa.UniqueConstraint("token_hash", name="uq_pending_provider_signup_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )


def downgrade() -> None:
    op.drop_table("pending_provider_signup")
    # No drop_constraint for the foreign key, for the reason f8b3d27a4c19
    # gives: batch mode rebuilds from copy_from and never reflects it, and
    # dropping the column takes the constraint with it anyway.
    with op.batch_alter_table(
        "oauth_login_attempt", copy_from=_oauth_login_attempt(*_new_columns())
    ) as batch:
        batch.drop_column("user_id")
        batch.drop_column("intent")
