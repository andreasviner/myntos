"""Accounting core: SAF-T-aligned chart of accounts, parties, tax tables and ledger.

Constraint naming follows ``myntos.db.base.NAMING_CONVENTION``, which
``op.create_table`` applies here just as it does to the models. That matters
for CHECK constraints specifically: their convention is
``ck_%(table_name)s_%(constraint_name)s``, so passing an already-prefixed
name yields ``ck_company_ck_company_...`` and — past 63 characters —
a silently truncated identifier. Check names are therefore given in the same
short form as in the models; unique, foreign key and primary key conventions
do not interpolate the given name, so those are spelled out in full.

Revision ID: 9f2a1c4b7e30
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f2a1c4b7e30"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SAF-T v1.40 monetary datatype (38 digits); scale 2 is øre.
MONEY = sa.Numeric(38, 2)
RATE = sa.Numeric(9, 6)


def upgrade() -> None:
    op.create_table(
        "company",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("orgnr", name="uq_company_orgnr"),
    )

    op.create_table(
        "financial_year",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("end_date > start_date", name="ends_after_start"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_financial_year_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financial_year"),
        sa.UniqueConstraint("company_id", "year", name="uq_financial_year_company_id"),
    )

    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("account_number", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column(
            "account_type",
            sa.Enum(
                "eiendel",
                "gjeld",
                "egenkapital",
                "inntekt",
                "kostnad",
                name="account_type",
            ),
            nullable=False,
        ),
        sa.Column("created_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_account_company_id_company", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account"),
        sa.UniqueConstraint("company_id", "account_number", name="uq_account_company_id"),
    )

    op.create_table(
        "account_grouping",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("grouping_category", sa.String(length=64), nullable=False),
        sa.Column("grouping_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_account_grouping_account_id_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_grouping"),
        sa.UniqueConstraint("account_id", "financial_year", name="uq_account_grouping_account_id"),
    )

    op.create_table(
        "account_opening_balance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("opening_debit", MONEY, nullable=False),
        sa.Column("opening_credit", MONEY, nullable=False),
        sa.CheckConstraint(
            "opening_debit >= 0 AND opening_credit >= 0",
            name="balances_are_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_account_opening_balance_account_id_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_opening_balance"),
        sa.UniqueConstraint(
            "account_id", "financial_year", name="uq_account_opening_balance_account_id"
        ),
    )

    op.create_table(
        "tax_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("standard_tax_code", sa.String(length=8), nullable=False),
        sa.Column("is_reportable", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_tax_code_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tax_code"),
        sa.UniqueConstraint("company_id", "code", name="uq_tax_code_company_id"),
    )

    op.create_table(
        "tax_rate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tax_code_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("rate", RATE, nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_to_after_from"),
        sa.CheckConstraint("rate >= 0", name="rate_is_non_negative"),
        sa.ForeignKeyConstraint(
            ["tax_code_id"],
            ["tax_code.id"],
            name="fk_tax_rate_tax_code_id_tax_code",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tax_rate"),
        sa.UniqueConstraint("tax_code_id", "valid_from", name="uq_tax_rate_tax_code_id"),
    )

    op.create_table(
        "party",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("party_type", sa.Enum("kunde", "leverandor", name="party_type"), nullable=False),
        sa.Column("party_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=True),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("balance_account_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["balance_account_id"],
            ["account.id"],
            name="fk_party_balance_account_id_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_party_company_id_company", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_party"),
        sa.UniqueConstraint("company_id", "party_type", "party_id", name="uq_party_company_id"),
    )

    op.create_table(
        "party_opening_balance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("opening_debit", MONEY, nullable=False),
        sa.Column("opening_credit", MONEY, nullable=False),
        sa.CheckConstraint(
            "opening_debit >= 0 AND opening_credit >= 0",
            name="balances_are_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["party.id"],
            name="fk_party_opening_balance_party_id_party",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_party_opening_balance"),
        sa.UniqueConstraint("party_id", "financial_year", name="uq_party_opening_balance_party_id"),
    )

    op.create_table(
        "dimension_type",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_dimension_type_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dimension_type"),
        sa.UniqueConstraint("company_id", "code", name="uq_dimension_type_company_id"),
    )

    op.create_table(
        "dimension_value",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dimension_type_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_type_id"],
            ["dimension_type.id"],
            name="fk_dimension_value_dimension_type_id_dimension_type",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dimension_value"),
        sa.UniqueConstraint(
            "dimension_type_id", "value", name="uq_dimension_value_dimension_type_id"
        ),
    )

    op.create_table(
        "journal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("journal_id", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("journal_type", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name="fk_journal_company_id_company", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_journal"),
        sa.UniqueConstraint("company_id", "journal_id", name="uq_journal_company_id"),
    )

    op.create_table(
        "gl_transaction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("journal_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("gl_posting_date", sa.Date(), nullable=False),
        sa.Column(
            "system_entry_date",
            sa.DateTime(timezone=True),
            # sa.func.now(), not sa.text("now()"): the literal is PostgreSQL
            # syntax and leaves a migrated SQLite database unable to insert a
            # voucher at all. func.now() renders now() on PostgreSQL and
            # CURRENT_TIMESTAMP on SQLite, and matches what the model declares.
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("voucher_type", sa.String(length=64), nullable=True),
        sa.Column("source_document_id", sa.String(length=128), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("period BETWEEN 1 AND 12", name="period_is_a_month"),
        sa.ForeignKeyConstraint(
            ["journal_id"],
            ["journal.id"],
            name="fk_gl_transaction_journal_id_journal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction"),
        sa.UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
    )

    op.create_table(
        "gl_transaction_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("debit", MONEY, nullable=False),
        sa.Column("credit", MONEY, nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("currency_amount", MONEY, nullable=True),
        sa.Column("exchange_rate", sa.Numeric(18, 8), nullable=True),
        sa.Column("tax_code_id", sa.Integer(), nullable=True),
        sa.Column("tax_rate", RATE, nullable=True),
        sa.Column("tax_base", MONEY, nullable=True),
        sa.Column("debit_tax_amount", MONEY, nullable=True),
        sa.Column("credit_tax_amount", MONEY, nullable=True),
        sa.CheckConstraint(
            "debit >= 0 AND credit >= 0",
            name="amounts_are_non_negative",
        ),
        sa.CheckConstraint("NOT (debit > 0 AND credit > 0)", name="one_side_only"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_gl_transaction_line_account_id_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["party.id"],
            name="fk_gl_transaction_line_party_id_party",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_code_id"],
            ["tax_code.id"],
            name="fk_gl_transaction_line_tax_code_id_tax_code",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_pk"],
            ["gl_transaction.id"],
            name="fk_gl_transaction_line_transaction_pk_gl_transaction",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_transaction_line"),
        sa.UniqueConstraint(
            "transaction_pk", "record_id", name="uq_gl_transaction_line_transaction_pk"
        ),
    )

    op.create_table(
        "gl_line_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("line_id", sa.Integer(), nullable=False),
        sa.Column("dimension_value_id", sa.Integer(), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_value_id"],
            ["dimension_value.id"],
            name="fk_gl_line_analysis_dimension_value_id_dimension_value",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["gl_transaction_line.id"],
            name="fk_gl_line_analysis_line_id_gl_transaction_line",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gl_line_analysis"),
        sa.UniqueConstraint("line_id", "dimension_value_id", name="uq_gl_line_analysis_line_id"),
    )


def downgrade() -> None:
    op.drop_table("gl_line_analysis")
    op.drop_table("gl_transaction_line")
    op.drop_table("gl_transaction")
    op.drop_table("journal")
    op.drop_table("dimension_value")
    op.drop_table("dimension_type")
    op.drop_table("party_opening_balance")
    op.drop_table("party")
    op.drop_table("tax_rate")
    op.drop_table("tax_code")
    op.drop_table("account_opening_balance")
    op.drop_table("account_grouping")
    op.drop_table("account")
    op.drop_table("financial_year")
    op.drop_table("company")
    # PostgreSQL keeps enum types after their tables are gone; drop them
    # explicitly so a downgrade/upgrade cycle does not fail on "type exists".
    sa.Enum(name="party_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_type").drop(op.get_bind(), checkfirst=True)
