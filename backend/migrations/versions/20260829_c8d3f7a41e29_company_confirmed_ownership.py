"""Confirmed ownership of a set of books.

Creating a company proves nothing: anyone can type nine digits into a form. The
columns added here record the moment somebody proved through Altinn that they
may act for the orgnr — the same evidence a submission later rests on.

The partial unique index is the point of the revision. ``orgnr`` stays unique
per organization, because two accounting firms may legitimately keep books for
the same client; what may not happen twice is a *confirmed* claim on one orgnr.
``uq_company_verified_orgnr`` says exactly that and leaves unconfirmed rows
alone. The domain checks the same thing first and answers with prose people can
act on (:class:`~myntos.domain.ledger.errors.CompanyAlreadyClaimedError`); the
index is what makes two confirmations racing produce one winner instead of two.

Partial indexes exist on both databases we run on — PostgreSQL since forever,
SQLite since 3.8 — so this is one index rather than a dialect fork. Both
``postgresql_where`` and ``sqlite_where`` are passed for that reason.

Nothing is backfilled. Every existing set of books is unconfirmed, which is the
truth: none of them has been through Altinn. The interface says "ubekreftet"
and offers the login; bookkeeping is not gated on it.

Revision ID: c8d3f7a41e29
Revises: a91b6d02e7c5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "c8d3f7a41e29"
down_revision: str | None = "a91b6d02e7c5"
branch_labels: None = None
depends_on: None = None

INDEX = "uq_company_verified_orgnr"
COLUMNS = ("verified_at", "verified_by_user_id", "altinn_party_id")


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` before this revision, plus anything passed in.

    SQLite rebuilds the table to add a foreign key, and a rebuild needs the
    whole definition — batch mode cannot reflect a constraint SQLite never
    stored by name.
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


def _added() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Integer(), nullable=True),
        sa.Column("altinn_party_id", sa.Integer(), nullable=True),
    )


def upgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        for column in _added():
            batch.add_column(column)
        # SET NULL and not CASCADE: the person who confirmed may leave the
        # organization, and the company stays confirmed — it was confirmed.
        batch.create_foreign_key(
            "fk_company_verified_by_user_id_app_user",
            "app_user",
            ["verified_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        INDEX,
        "company",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text("verified_at IS NOT NULL"),
        sqlite_where=sa.text("verified_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="company")
    with op.batch_alter_table("company", copy_from=_company_table(*_added())) as batch:
        for name in COLUMNS:
            batch.drop_column(name)
