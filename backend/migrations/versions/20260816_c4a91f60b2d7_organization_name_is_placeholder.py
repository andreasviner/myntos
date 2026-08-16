"""Mark organization names that Myntos invented rather than a person chose.

Signup does not ask for an organization name — most people signing up do not yet
think of themselves as having one — so it makes up "Ola Nordmann (privat)". That
placeholder then sat in the header next to a company name fetched from
Enhetsregisteret, which reads as though the lookup had failed.

Whether a name was invented cannot be recomputed after the fact: the user may
have changed their own name since, and comparing against the generated string
would guess wrong for exactly the people who did. So it is stored.

Existing rows get ``false``. That is the conservative answer — it means nothing
already in the database is ever renamed automatically — and it is correct for
every organization whose owner did type a name. The few placeholder rows created
before this revision keep their name until someone renames them by hand, which
is the same outcome they had before.

Added with ``server_default`` so existing rows get a value, then dropped: the
model declares a Python-side default only, and a lingering server default shows
up as a phantom diff on every later autogenerate (see revision 7b4e0d92a1c8).

Revision ID: c4a91f60b2d7
Revises: 7b4e0d92a1c8
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "c4a91f60b2d7"
down_revision: str | None = "7b4e0d92a1c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "name_is_placeholder"


def _organization_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``organization`` before this revision, plus anything passed in.

    Spelled out rather than reflected, so the table SQLite rebuilds keeps the
    constraint names the models declare (see revisions 3d7c15ba90e4 and
    7b4e0d92a1c8).
    """
    return sa.Table(
        "organization",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *extra,
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("slug", name="uq_organization_slug"),
    )


def upgrade() -> None:
    with op.batch_alter_table("organization", copy_from=_organization_table()) as batch:
        # sa.false() renders "false" on PostgreSQL and "0" on SQLite; a literal 0
        # would be rejected by the former as an integer.
        batch.add_column(sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("organization") as batch:
        batch.alter_column(COLUMN, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(
        "organization",
        copy_from=_organization_table(sa.Column(COLUMN, sa.Boolean(), nullable=False)),
    ) as batch:
        batch.drop_column(COLUMN)
