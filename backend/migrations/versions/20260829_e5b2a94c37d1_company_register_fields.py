"""What Enhetsregisteret says about a company, cached on the company.

Every field here already existed in the register; what was missing was anywhere
to put it, so it was being asked for, shown once during signup and thrown away.
Somebody then typed the næringskode into a filing by hand.

The set is deliberately small. Only what something in the system uses is stored
— the næringskode a næringsspesifikasjon asks for, the contact details a
salgsdokument may carry, and the main unit a subunit belongs to. The rest of
what the register holds (board, revisor, kapital, formål, historical names) is
fetched live for display and never persisted: it changes without telling us, and
some of it is other people's personal data, which is not ours to keep a stale
copy of.

``register_synced_at`` is what makes the rest a cache rather than a second
opinion. NULL means the register has never been read for this company, which is
true of every row that exists today — nothing is backfilled, because a
backfill would be one HTTP request per company inside a migration, and the
refresh happens on the next confirmation or on demand.

``parent_orgnr`` is nine characters like ``orgnr``, but deliberately carries no
CHECK constraint: it is copied from the register's ``overordnetEnhet`` and a
register that one day answers with something else should leave a company
unrefreshed, not a migration unrunnable.

Revision ID: e5b2a94c37d1
Revises: c8d3f7a41e29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "e5b2a94c37d1"
down_revision: str | None = "c8d3f7a41e29"
branch_labels: None = None
depends_on: None = None

COLUMNS = (
    "industry_code",
    "industry_description",
    "website",
    "phone",
    "email",
    "parent_orgnr",
    "register_synced_at",
)


def _added() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("industry_code", sa.String(length=16), nullable=True),
        sa.Column("industry_description", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        # 320 to match app_user.email: the same addresses go in both.
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("parent_orgnr", sa.String(length=9), nullable=True),
        sa.Column("register_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` before this revision, plus anything passed in.

    Spelled out for the same reason every other revision on this table spells it
    out: SQLite rebuilds rather than alters, and a rebuild needs the whole
    definition.

    That includes ``uq_company_verified_orgnr``. A rebuild recreates exactly
    what this object describes and nothing else, so an index left out here is an
    index silently dropped — and this one is the only thing stopping two
    organizations from both confirming the same orgnr. It would have gone
    missing on SQLite while surviving on PostgreSQL, which is the worst shape a
    schema divergence can take.
    """
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
        sa.Column("ki_enabled", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Integer(), nullable=True),
        sa.Column("altinn_party_id", sa.Integer(), nullable=True),
        *extra,
        sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_company_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["app_user.id"],
            name="fk_company_verified_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
        sa.Index(
            "uq_company_verified_orgnr",
            "orgnr",
            unique=True,
            sqlite_where=sa.text("verified_at IS NOT NULL"),
            postgresql_where=sa.text("verified_at IS NOT NULL"),
        ),
    )


def upgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        for column in _added():
            batch.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table(*_added())) as batch:
        for name in COLUMNS:
            batch.drop_column(name)
