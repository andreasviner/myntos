"""Opening balance and årsavslutning: the year log, and the accounts both need.

Two things.

**``financial_year_event``**, the append-only log of every close and reopening
of a financial year: who, when, the result and tax the close was made on, and
the reason a reopening gave. ``financial_year.closed_at`` only says whether a
year is locked now, and clearing it on a reopening would erase the only trace
that it was ever closed.

**Seven accounts for existing companies**, because the chart is seeded per
company at creation (see ``a2c7f31b9d64``) and the new flows post to accounts the
seed did not have:

- 2000 Aksjekapital, which every AS's opening balance carries;
- 2080 Udekket tap and 8990 Udekket tap, where an AS's loss goes when annen
  egenkapital cannot cover it;
- 2500 Betalbar skatt, ikke fastsatt and 8300 Betalbar skatt, the AS's tax cost;
- 8800 Årsresultat overført egenkapital (ENK) and 8960 Overføringer annen
  egenkapital (AS), the disposition.

Each pair is in Skatteetaten's 2025-2026 grouping list; the three disposition
accounts share its one post for it, ``resultatDisponeringForSAF-T``/8800.

**What it infers:** the year the books start, as the earliest financial year,
for ``created_date``, and that every year already open needs a grouping row for
each new account, because the SAF-T export cannot place an account without one.

**What it refuses to guess:** whether any year is already closed. No row of
``financial_year.closed_at`` is touched and no event is invented, since nothing
before this migration could close a year and a close is a decision somebody
makes. Nor does it write an opening balance from anything: the books that came
from elsewhere did not tell us their balance. A company with no financial year
is skipped rather than dated from today, and an account number a company
already holds is left exactly as it is.

Written against Core tables for the reason ``d4e1a83b62c7`` gives. The
downgrade drops the log and removes only accounts nothing has been posted to.

Revision ID: c3f9a2d71e58
Revises: c5e8a17d3f42
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "c3f9a2d71e58"
down_revision: str | None = "c5e8a17d3f42"
branch_labels: None = None
depends_on: None = None

# Copied rather than imported from myntos.domain.ledger.chart, for the reason
# b7d40e2c9a15 gives: a migration keeps meaning what it meant on its day.
# (number, description, account_type, grouping_category, grouping_code)
ACCOUNTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("2000", "Aksjekapital", "egenkapital", "egenkapital", "2000"),
    ("2080", "Udekket tap", "egenkapital", "egenkapital", "2080"),
    ("2500", "Betalbar skatt, ikke fastsatt", "gjeld", "kortsiktigGjeld", "2500"),
    ("8300", "Betalbar skatt", "kostnad", "skattekostnad", "8300"),
    (
        "8800",
        "Årsresultat overført egenkapital",
        "kostnad",
        "resultatDisponeringForSAF-T",
        "8800",
    ),
    (
        "8960",
        "Overføringer annen egenkapital",
        "kostnad",
        "resultatDisponeringForSAF-T",
        "8800",
    ),
    ("8990", "Udekket tap", "kostnad", "resultatDisponeringForSAF-T", "8800"),
)

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
    sa.column("is_active", sa.Boolean),
)
account_grouping = sa.table(
    "account_grouping",
    sa.column("account_id", sa.Integer),
    sa.column("financial_year", sa.Integer),
    sa.column("grouping_category", sa.String),
    sa.column("grouping_code", sa.String),
)
gl_transaction_line = sa.table("gl_transaction_line", sa.column("account_id", sa.Integer))


def upgrade() -> None:
    op.create_table(
        "financial_year_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("result", sa.Numeric(38, 2), nullable=True),
        sa.Column("tax", sa.Numeric(38, 2), nullable=True),
    )
    op.create_index(
        "ix_financial_year_event_company",
        "financial_year_event",
        ["company_id", "financial_year"],
    )

    bind = op.get_bind()
    for (company_id,) in bind.execute(sa.select(company.c.id)).fetchall():
        years = [
            int(year)
            for (year,) in bind.execute(
                sa.select(financial_year.c.year).where(financial_year.c.company_id == company_id)
            ).fetchall()
        ]
        if not years:
            continue
        opens = date(min(years), 1, 1)
        for number, description, account_type, category, code in ACCOUNTS:
            present = bind.execute(
                sa.select(account.c.id).where(
                    account.c.company_id == company_id, account.c.account_number == number
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
                    account.c.company_id == company_id, account.c.account_number == number
                )
            ).scalar()
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
    op.drop_index("ix_financial_year_event_company", table_name="financial_year_event")
    op.drop_table("financial_year_event")
