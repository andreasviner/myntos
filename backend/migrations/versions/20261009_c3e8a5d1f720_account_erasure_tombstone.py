"""Let a deleted account leave a tombstone behind instead of a hole in the books.

Account deletion (GDPR art. 17) removes the person: credentials, federated and
government identities, sessions, memberships, invitations addressed to them.
What it cannot remove is the fact that they registered entries in somebody's
books. ``gl_transaction.created_by_user_id`` and the other actor columns are
``ON DELETE SET NULL``, so deleting the ``app_user`` row would quietly turn
"Kari entered this" into «ukjent» on every voucher she ever posted, and the
SAF-T ``SourceID`` exported for those vouchers would stop meaning anyone. The
books have to keep answering who registered an entry for as long as they are
kept, which is the company's obligation and not the person's to waive.

So an account whose id the books still reference is kept as a tombstone: the
id and the name stay, everything else is wiped, and this column records when.
An account no retained record points at is deleted outright, and never gets
here.

**What it infers:** nothing. Every existing row gets NULL, which is true: no
account has been deleted through this path before, and the only earlier way to
delete one (a hand-written ``DELETE``) left no row to mark.

**What it refuses to guess:** whether any existing row *should* be a tombstone.
An inactive account (``is_active = false``) is not one: deactivation is a
reversible lock, and reading it as an erasure request would wipe somebody who
asked for nothing of the kind.

Revision ID: c3e8a5d1f720
Revises: b7d40e2c9a15
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "c3e8a5d1f720"
down_revision: str | None = "b7d40e2c9a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "deleted_at"


def _app_user(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``app_user`` as ``3d7c15ba90e4`` created it, plus ``extra``.

    Spelled out for the downgrade, where SQLite rebuilds the table: reflection
    would lose the CHECK constraint's name, and with it the rule that an email
    address is stored in lower case.
    """
    return sa.Table(
        "app_user",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *extra,
        sa.CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )


def upgrade() -> None:
    # A nullable column with no default is a plain ALTER TABLE on both
    # PostgreSQL and SQLite, so no rebuild on the way up.
    op.add_column("app_user", sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Tombstones survive the downgrade as rows with no password, no identities
    # and a placeholder address; they simply stop saying when they were made.
    with op.batch_alter_table(
        "app_user",
        copy_from=_app_user(sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True)),
    ) as batch:
        batch.drop_column(COLUMN)
