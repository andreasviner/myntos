"""Give existing companies the settlement, currency and loan accounts.

Five accounts, added because the cash-flow catalogue grew five kinds of payment
it could not previously express: paying or reclaiming a filed MVA-melding
(2740), paying on the forskuddstrekk withheld from a salary (2600), borrowing
and repaying principal (2220), and the difference the exchange rate makes
between an invoice and its payment (8060 and 8160).

Like ``d4e1a83b62c7`` before it, this exists because the chart is seeded **per
company at creation**. Adding rows to ``STANDARD_ACCOUNTS`` reaches only
companies created afterwards, so every existing set of books would offer the new
categories in the interface and then fail the account lookup when one was
chosen, which is worse than not offering them at all.

What it infers: the year the books start, taken as the earliest open financial
year, so ``created_date`` lands where the seeding would have put it. What it
refuses to guess: anything about a company with no financial year at all, which
is skipped rather than dated from today.

The two P&L accounts are grouped onto the næringsspesifikasjon posts their
neighbours already use — 8060 with 8050 under ``finansinntekt``, 8160 with 8150
under ``finanskostnad`` — the same way 3010 and 3020 group under 3000. The three
balance-sheet accounts get "NA", which is what SAF-T prescribes for them.

**Written against Core tables rather than raw SQL strings**, for the reason
``d4e1a83b62c7`` records: a data migration binds values, and SQLite will accept
a string where a date or an enum belongs while asyncpg refuses both.

Nothing is removed on downgrade beyond what this added, and an account that has
been posted to is kept: a voucher is history and its account has to keep
existing for the line to be readable.

Revision ID: a2c7f31b9d64
Revises: f8b3d27a4c19
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "a2c7f31b9d64"
down_revision: str | None = "f8b3d27a4c19"
branch_labels: None = None
depends_on: None = None

# (number, description, account_type, grouping_category, grouping_code)
ACCOUNTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("2220", "Gjeld til kredittinstitusjoner", "gjeld", "NA", "NA"),
    ("2600", "Forskuddstrekk", "gjeld", "NA", "NA"),
    ("2740", "Oppgjørskonto merverdiavgift", "gjeld", "NA", "NA"),
    ("8060", "Valutagevinst", "inntekt", "finansinntekt", "8050"),
    ("8160", "Valutatap", "kostnad", "finanskostnad", "8150"),
)

# The enum already exists in the database; create_type=False keeps alembic from
# trying to define it a second time on PostgreSQL.
_ACCOUNT_TYPE = sa.Enum(
    "eiendel",
    "gjeld",
    "egenkapital",
    "inntekt",
    "kostnad",
    name="account_type",
    create_type=False,
)

company = sa.table("company", sa.column("id", sa.Integer))
financial_year = sa.table(
    "financial_year",
    sa.column("company_id", sa.Integer),
    sa.column("year", sa.Integer),
)
account = sa.table(
    "account",
    sa.column("id", sa.Integer),
    sa.column("company_id", sa.Integer),
    sa.column("account_number", sa.String),
    sa.column("description", sa.String),
    sa.column("account_type", _ACCOUNT_TYPE),
    sa.column("created_date", sa.Date),
    # NOT NULL with a Python-side default only, so a migration has to say it.
    sa.column("is_active", sa.Boolean),
)
account_grouping = sa.table(
    "account_grouping",
    sa.column("account_id", sa.Integer),
    sa.column("financial_year", sa.Integer),
    sa.column("grouping_category", sa.String),
    sa.column("grouping_code", sa.String),
)
gl_transaction_line = sa.table(
    "gl_transaction_line",
    sa.column("account_id", sa.Integer),
)


def upgrade() -> None:
    bind = op.get_bind()
    for (company_id,) in bind.execute(sa.select(company.c.id)).fetchall():
        start = bind.execute(
            sa.select(sa.func.min(financial_year.c.year)).where(
                financial_year.c.company_id == company_id
            )
        ).scalar()
        if start is None:
            continue
        opens = date(int(start), 1, 1)
        years = [
            year
            for (year,) in bind.execute(
                sa.select(financial_year.c.year).where(financial_year.c.company_id == company_id)
            ).fetchall()
        ]

        for number, description, account_type, category, code in ACCOUNTS:
            present = bind.execute(
                sa.select(account.c.id).where(
                    account.c.company_id == company_id,
                    account.c.account_number == number,
                )
            ).scalar()
            if present is not None:
                continue
            bind.execute(
                account.insert().values(
                    company_id=company_id,
                    account_number=number,
                    description=description,
                    account_type=account_type,
                    created_date=opens,
                    is_active=True,
                )
            )
            account_id = bind.execute(
                sa.select(account.c.id).where(
                    account.c.company_id == company_id,
                    account.c.account_number == number,
                )
            ).scalar()
            # _open_financial_year gives every account a grouping row per year,
            # so one added afterwards needs them for the years already open, or
            # the SAF-T export has an account it cannot place.
            for year in years:
                bind.execute(
                    account_grouping.insert().values(
                        account_id=account_id,
                        financial_year=year,
                        grouping_category=category,
                        grouping_code=code,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    numbers = [number for number, *_ in ACCOUNTS]
    unused = sa.select(account.c.id).where(
        account.c.account_number.in_(numbers),
        account.c.id.notin_(sa.select(gl_transaction_line.c.account_id)),
    )
    bind.execute(account_grouping.delete().where(account_grouping.c.account_id.in_(unused)))
    bind.execute(account.delete().where(account.c.id.in_(unused)))
