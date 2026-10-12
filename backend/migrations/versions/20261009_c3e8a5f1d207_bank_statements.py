"""Imported camt.053 bank statements, their lines, and the link to a voucher.

Three new tables and nothing else: ``bank_statement_file`` (the archived file,
kept like every other bilag), ``bank_statement`` (one account over one period)
and ``bank_statement_line`` (one movement of money, and the voucher that books
it once somebody has said so). See docs/integrations/bank.md.

**What it infers:** nothing. No statement has ever been imported, so there is
no row to create and no existing table is touched.

**What it refuses to guess:** which existing vouchers are the bookings of bank
movements. A register of vouchers entered before this revision is exactly what
the import will be reconciled against, and pre-filling links from amounts and
dates would be the matching algorithm deciding on the person's behalf, which is
the one thing it is not allowed to do. Every voucher starts unmatched.

Revision ID: c3e8a5f1d207
Revises: c3e8a5d1f720
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a5f1d207"
down_revision: str | None = "c3e8a5d1f720"
branch_labels: None = None
depends_on: None = None

MONEY = sa.Numeric(38, 2)


def upgrade() -> None:
    op.create_table(
        "bank_statement_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("camt_version", sa.String(length=16), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("byte_size > 0", name="byte_size_is_positive"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_bank_statement_file_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["app_user.id"],
            name="fk_bank_statement_file_uploaded_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bank_statement_file"),
        sa.UniqueConstraint("company_id", "sha256", name="uq_bank_statement_file_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_bank_statement_file_storage_key"),
    )
    op.create_table(
        "bank_statement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("statement_ref", sa.String(length=128), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("opening_balance", MONEY, nullable=False),
        sa.Column("closing_balance", MONEY, nullable=False),
        sa.Column("lines_new", sa.Integer(), nullable=False),
        sa.Column("lines_known", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("to_date >= from_date", name="ends_after_start"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_bank_statement_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["bank_statement_file.id"],
            name="fk_bank_statement_file_id_bank_statement_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["company_bank_account.id"],
            name="fk_bank_statement_bank_account_id_company_bank_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bank_statement"),
    )
    op.create_index(
        "ix_bank_statement_account_period", "bank_statement", ["bank_account_id", "from_date"]
    )
    op.create_table(
        "bank_statement_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("statement_id", sa.Integer(), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), nullable=False),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("remittance", sa.String(length=1000), nullable=True),
        sa.Column("kid", sa.String(length=64), nullable=True),
        sa.Column("end_to_end_id", sa.String(length=64), nullable=True),
        sa.Column("bank_reference", sa.String(length=128), nullable=True),
        sa.Column("additional_info", sa.String(length=500), nullable=True),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("match_kind", sa.String(length=16), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("amount > 0", name="amount_is_positive"),
        # The model's Enum(native_enum=False) renders as this CHECK; spelled out
        # so the migration does not depend on how a later SQLAlchemy names it.
        sa.CheckConstraint("direction IN ('inn', 'ut')", name="cash_flow_direction"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_bank_statement_line_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["bank_statement.id"],
            name="fk_bank_statement_line_statement_id_bank_statement",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["company_bank_account.id"],
            name="fk_bank_statement_line_bank_account_id_company_bank_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_pk"],
            ["gl_transaction.id"],
            name="fk_bank_statement_line_transaction_pk_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["matched_by_user_id"],
            ["app_user.id"],
            name="fk_bank_statement_line_matched_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bank_statement_line"),
        sa.UniqueConstraint(
            "bank_account_id", "dedup_key", name="uq_bank_statement_line_dedup_key"
        ),
        sa.UniqueConstraint("transaction_pk", name="uq_bank_statement_line_transaction_pk"),
    )
    op.create_index(
        "ix_bank_statement_line_company_date",
        "bank_statement_line",
        ["company_id", "booking_date"],
    )


def downgrade() -> None:
    # Drops the reconciliation work and the archive's index of statement files.
    # The files themselves stay in storage: a downgrade is not a deletion of
    # primary documentation, and nothing here may become one.
    op.drop_index("ix_bank_statement_line_company_date", table_name="bank_statement_line")
    op.drop_table("bank_statement_line")
    op.drop_index("ix_bank_statement_account_period", table_name="bank_statement")
    op.drop_table("bank_statement")
    op.drop_table("bank_statement_file")
