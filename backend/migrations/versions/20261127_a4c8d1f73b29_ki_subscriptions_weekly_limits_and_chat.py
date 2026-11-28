"""KI subscriptions, weekly token usage, monthly billing invoices, and KI chat.

Adds five company-scoped tables:

- ``ki_subscription``: monthly KI plan billed by invoice (active/canceling/canceled).
- ``ki_weekly_usage``: paid-level tokens spent per company per ISO week (Monday in Europe/Oslo).
- ``ki_billing_invoice``: record of each monthly section 5-1 invoice issued for a KI subscription.
- ``ki_chat_thread`` and ``ki_chat_message``: saved KI chat conversations per company.

**What it infers:** nothing. Existing ``ki_period`` rows are untouched and keep
working as one-off paid months alongside the new tables, which start empty.

**What it refuses to guess:** subscriptions, billing emails, or invoices for
existing companies. A company owner explicitly chooses a monthly plan when ready.

Revision ID: a4c8d1f73b29
Revises: 659e80f0c3f5
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c8d1f73b29"
down_revision: str | None = "659e80f0c3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ki_subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("weekly_tokens_allowance", sa.BigInteger(), nullable=False),
        sa.Column("price_excl_vat", sa.Numeric(38, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(38, 2), nullable=False),
        sa.Column("price_incl_vat", sa.Numeric(38, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("billing_email", sa.String(length=320), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_ki_subscription_company_id"),
    )
    op.create_index(
        "ix_ki_subscription_status_end",
        "ki_subscription",
        ["status", "current_period_end"],
    )

    op.create_table(
        "ki_weekly_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "week_start", name="uq_ki_weekly_usage_company_week"),
    )
    op.create_index("ix_ki_weekly_usage_company_id", "ki_weekly_usage", ["company_id"])

    op.create_table(
        "ki_billing_invoice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("seller_company_id", sa.Integer(), nullable=True),
        sa.Column("seller_document_id", sa.Integer(), nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("amount_excl_vat", sa.Numeric(38, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(38, 2), nullable=False),
        sa.Column("amount_incl_vat", sa.Numeric(38, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("delivery_method", sa.String(length=16), nullable=False),
        sa.Column("delivery_status", sa.String(length=16), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recipient_email", sa.String(length=320), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["ki_subscription.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["seller_company_id"], ["company.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["seller_document_id"], ["sales_document.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "period_start", "period_end", name="uq_ki_billing_invoice_period"
        ),
    )
    op.create_index("ix_ki_billing_invoice_company_id", "ki_billing_invoice", ["company_id"])

    op.create_table(
        "ki_chat_thread",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ki_chat_thread_company_updated", "ki_chat_thread", ["company_id", "updated_at"]
    )

    op.create_table(
        "ki_chat_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=True),
        sa.Column("requested_level", sa.String(length=16), nullable=True),
        sa.Column("tokens_used", sa.BigInteger(), nullable=False),
        sa.Column("notices", _JSON_DOC, nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["ki_chat_thread.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ki_chat_message_thread_id", "ki_chat_message", ["thread_id"])
    op.create_index("ix_ki_chat_message_company_id", "ki_chat_message", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_ki_chat_message_company_id", table_name="ki_chat_message")
    op.drop_index("ix_ki_chat_message_thread_id", table_name="ki_chat_message")
    op.drop_table("ki_chat_message")
    op.drop_index("ix_ki_chat_thread_company_updated", table_name="ki_chat_thread")
    op.drop_table("ki_chat_thread")
    op.drop_index("ix_ki_billing_invoice_company_id", table_name="ki_billing_invoice")
    op.drop_table("ki_billing_invoice")
    op.drop_index("ix_ki_weekly_usage_company_id", table_name="ki_weekly_usage")
    op.drop_table("ki_weekly_usage")
    op.drop_index("ix_ki_subscription_status_end", table_name="ki_subscription")
    op.drop_table("ki_subscription")
