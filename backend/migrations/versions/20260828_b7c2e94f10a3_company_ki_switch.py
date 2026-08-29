"""A per-company switch for whether documents may be shown to a model.

Defaults to enabled, which is what every existing company has been doing since
the model was wired in, so the migration changes no behaviour on its own. What
it adds is the ability to say no: an accountant keeping books for a client whose
personvernerklæring does not cover it can turn the whole thing off for that set
of books without turning it off for the other nineteen.

``sa.true()`` and not ``sa.text("1")``: the former renders ``true`` on
PostgreSQL and ``1`` on SQLite, and PostgreSQL rejects a bare integer as the
default for a boolean column. The suite runs on SQLite, so this is a difference
the tests cannot see and only production can.

The server default is added and then dropped, the same way every other boolean
on this table was added (``7b4e0d92a1c8``). It exists only to give the rows that
already exist a value; the model declares a Python-side default, and a default
left behind in the database shows up as a phantom diff on every autogenerate
from here on.

Revision ID: b7c2e94f10a3
Revises: a3f18c76d2b4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "b7c2e94f10a3"
down_revision: str | None = "a3f18c76d2b4"
branch_labels: None = None
depends_on: None = None

COLUMN = "ki_enabled"


# SQLite cannot alter a column's default in place, so alembic rebuilds the table
# and has to be told what it looked like first: ``company`` as of the previous
# revision.


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` before this revision, plus anything passed in."""
    return sa.Table(
        "company",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("vat_registered", sa.Boolean(), nullable=False),
        sa.Column("in_foretaksregisteret", sa.Boolean(), nullable=False),
        sa.Column("organisation_form", sa.String(length=16), nullable=True),
        sa.Column("bank_account", sa.String(length=34), nullable=True),
        *extra,
        sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_company_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        batch.add_column(sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("company") as batch:
        batch.alter_column(COLUMN, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table(
        "company",
        copy_from=_company_table(sa.Column(COLUMN, sa.Boolean(), nullable=False)),
    ) as batch:
        batch.drop_column(COLUMN)
