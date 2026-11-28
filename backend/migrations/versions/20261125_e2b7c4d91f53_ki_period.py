"""KI plans: ``ki_period``, a month of the paid levels bought by a company.

One row per checkout started, paid or not (myntos.domain.ki.models). Nothing in
the existing tables changes: ``company.plan`` keeps saying ``free``, because the
books are free and stay free; what a company bought is read from this table.

**What it infers:** nothing. No company has bought anything before this table
exists, so it starts empty.

**What it refuses to guess:** a month for anybody. A company that wants the
paid levels buys one; none is handed out by a migration.

Revision ID: e2b7c4d91f53
Revises: d9f1a4c82b31
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7c4d91f53"
down_revision: str | None = "d9f1a4c82b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ki_period",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("mollie_payment_id", sa.String(length=80), nullable=True),
        sa.Column("mollie_mode", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(38, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(38, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("tokens_allowance", sa.BigInteger(), nullable=False),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bought_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bought_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
        sa.UniqueConstraint("mollie_payment_id"),
    )
    op.create_index("ix_ki_period_company_id", "ki_period", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_ki_period_company_id", table_name="ki_period")
    op.drop_table("ki_period")
