"""Many kontonummer per company, instead of one column holding the first.

``company.bank_account`` could hold exactly one account, so a business with a
driftskonto and a skattetrekkskonto — which skattebetalingsloven § 5-12 requires
be kept apart from the operating money — had to keep the second one somewhere
that was not Myntos. This moves the number into ``company_bank_account``, one
row per account, and lets a salgsdokument name which of them it is paid into.

What it infers, and it is the only inference here: **the account that was in the
column becomes the company's primary account**, unlabelled. That is exactly what
it was — the number every invoice carried — so nothing about any existing
company changes. A company whose column was NULL gets no row, which is also what
it had: no account, and no invoice it could issue.

What it refuses to guess:

- **A label.** The column never held one. Naming an account "Driftskonto"
  because it happens to be the only one would be putting words in the owner's
  mouth on a page about their bank.
- **Which account an already-issued document was paid into.**
  ``sales_document.bank_account_id`` is left NULL on every existing row. The
  number those documents carry is in ``seller_bank_account`` and is what they
  said when they were issued; back-filling a foreign key to today's account row
  would claim a link that was never made, and would quietly start reading
  today's number onto yesterday's invoice the day somebody corrects a typo.
  NULL means "whichever is primary", which for every existing company is the
  account this migration just created from the number those documents already
  carry.

The downgrade puts the primary account back in the column and drops the rest on
the floor, because the column has room for one. That is lossy and cannot not be:
it is the shape this revision exists to leave behind.

Revision ID: e7a1c93f6b28
Revises: d1a7f2c6b483
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "e7a1c93f6b28"
down_revision: str | None = "d1a7f2c6b483"
branch_labels: None = None
depends_on: None = None

COLUMN = "bank_account"


# SQLite cannot drop a column in place, so alembic rebuilds the table and has to
# be told what it looked like first. Reflection is not enough: SQLAlchemy does
# not reflect CHECK constraints from SQLite, so a reflected rebuild would
# silently lose ``orgnr_is_nine_digits``. Hence ``copy_from``, as in b7c2e94f10a3.


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` at this revision, minus anything passed in."""
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
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("ki_enabled", sa.Boolean(), nullable=False),
        sa.Column("industry_code", sa.String(length=16), nullable=True),
        sa.Column("industry_description", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("parent_orgnr", sa.String(length=9), nullable=True),
        sa.Column("register_synced_at", sa.DateTime(timezone=True), nullable=True),
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
    )


def _bank_account_column() -> sa.Column[object]:
    return sa.Column(COLUMN, sa.String(length=34), nullable=True)


def upgrade() -> None:
    op.create_table(
        "company_bank_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("account", sa.String(length=11), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_company_bank_account_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_bank_account"),
        sa.UniqueConstraint("company_id", "account", name="uq_company_bank_account_number"),
    )
    # Partial, so it binds the primary and leaves the rest alone. Written the
    # same way on both dialects: ``is_primary`` is a boolean on PostgreSQL and
    # 0/1 on SQLite, and a bare column name is a valid index predicate in each.
    op.create_index(
        "uq_company_bank_account_primary",
        "company_bank_account",
        ["company_id"],
        unique=True,
        sqlite_where=sa.text("is_primary"),
        postgresql_where=sa.text("is_primary"),
    )

    # The number every existing invoice already carries, promoted to the account
    # it always was. ``true``/``sa.true()`` rather than ``1``: PostgreSQL rejects
    # a bare integer for a boolean, and the suite runs on SQLite, so this is a
    # difference only production would find.
    company = sa.table(
        "company",
        sa.column("id", sa.Integer),
        sa.column(COLUMN, sa.String),
    )
    op.execute(
        sa.insert(
            sa.table(
                "company_bank_account",
                sa.column("company_id", sa.Integer),
                sa.column("account", sa.String),
                sa.column("is_primary", sa.Boolean),
            )
        ).from_select(
            ["company_id", "account", "is_primary"],
            sa.select(company.c.id, company.c[COLUMN], sa.true()).where(
                company.c[COLUMN].isnot(None), company.c[COLUMN] != ""
            ),
        )
    )

    with op.batch_alter_table("company", copy_from=_company_table(_bank_account_column())) as batch:
        batch.drop_column(COLUMN)

    # NULL on every existing row, on purpose: see the docstring. The column says
    # which account a document *chose*, and no existing document chose one.
    with op.batch_alter_table("sales_document") as batch:
        batch.add_column(sa.Column("bank_account_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sales_document_bank_account_id_company_bank_account",
            "company_bank_account",
            ["bank_account_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sales_document") as batch:
        batch.drop_constraint(
            "fk_sales_document_bank_account_id_company_bank_account", type_="foreignkey"
        )
        batch.drop_column("bank_account_id")

    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        batch.add_column(_bank_account_column())

    # Only the primary survives; a company's second and third accounts have
    # nowhere to go in a single column. Lossy by construction, which is the
    # whole reason for the upgrade above.
    accounts = sa.table(
        "company_bank_account",
        sa.column("company_id", sa.Integer),
        sa.column("account", sa.String),
        sa.column("is_primary", sa.Boolean),
    )
    company = sa.table("company", sa.column("id", sa.Integer), sa.column(COLUMN, sa.String))
    op.execute(
        company.update().values(
            **{
                COLUMN: sa.select(accounts.c.account)
                .where(accounts.c.company_id == company.c.id, accounts.c.is_primary)
                .scalar_subquery()
            }
        )
    )

    op.drop_index("uq_company_bank_account_primary", table_name="company_bank_account")
    op.drop_table("company_bank_account")
