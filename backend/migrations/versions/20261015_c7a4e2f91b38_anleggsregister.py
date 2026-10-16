"""Anleggsregister: fixed assets, opening saldi, and what each depreciation voucher posted.

Three new tables for ``myntos.domain.anlegg`` (docs/anlegg.md):

``fixed_asset``
    One row per driftsmiddel: saldogruppe, account, delivery date, cost, the
    public grant and whether it comes off the cost, and the disposal.
``fixed_asset_opening``
    The tax saldi a company brings from the old program at the start of the
    register's first year, per collective group and account, per building for
    group j, and the gevinst- og tapskonto.
``fixed_asset_depreciation``
    Per depreciation voucher, the amount per saldo and account, so next year's
    saldo is built on the choice the company made.

**What it infers:** nothing. The tables start empty.

**What it refuses to guess:** an existing company's driftsmidler from its
ledger. A balance on 1200 does not say which saldogruppe its parts belong to,
when each was delivered, or what the old program's tax saldo was; the person
registers them, and the opening saldi come from last year's skattemelding. No
posted voucher is touched.

Revision ID: c7a4e2f91b38
Revises: 5b2e8d4f1a63
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7a4e2f91b38"
down_revision: str | None = "5b2e8d4f1a63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 2)


def upgrade() -> None:
    op.create_table(
        "fixed_asset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("saldogruppe", sa.String(length=16), nullable=False),
        sa.Column("rate_variant", sa.String(length=32), nullable=True),
        sa.Column("account_number", sa.String(length=32), nullable=False),
        sa.Column("acquired_on", sa.Date(), nullable=False),
        sa.Column("cost", MONEY, nullable=False),
        sa.Column("grant", MONEY, nullable=False),
        sa.Column("grant_reduces_cost", sa.Boolean(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("building_id", sa.Integer(), nullable=True),
        sa.Column("opening_year", sa.Integer(), nullable=True),
        sa.Column("opening_value", MONEY, nullable=True),
        sa.Column("useful_life_years", sa.Integer(), nullable=True),
        sa.Column("disposed_on", sa.Date(), nullable=True),
        sa.Column("disposal_proceeds", MONEY, nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["building_id"], ["fixed_asset.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fixed_asset_company", "fixed_asset", ["company_id"])

    op.create_table(
        "fixed_asset_opening",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("saldogruppe", sa.String(length=16), nullable=False),
        sa.Column("building_id", sa.Integer(), nullable=True),
        sa.Column("account_number", sa.String(length=32), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["building_id"], ["fixed_asset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fixed_asset_opening_company", "fixed_asset_opening", ["company_id"])

    op.create_table(
        "fixed_asset_depreciation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("saldo_key", sa.String(length=64), nullable=False),
        sa.Column("saldogruppe", sa.String(length=16), nullable=False),
        sa.Column("account_number", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fixed_asset_depreciation_company",
        "fixed_asset_depreciation",
        ["company_id", "year"],
    )


def downgrade() -> None:
    op.drop_index("ix_fixed_asset_depreciation_company", table_name="fixed_asset_depreciation")
    op.drop_table("fixed_asset_depreciation")
    op.drop_index("ix_fixed_asset_opening_company", table_name="fixed_asset_opening")
    op.drop_table("fixed_asset_opening")
    op.drop_index("ix_fixed_asset_company", table_name="fixed_asset")
    op.drop_table("fixed_asset")
