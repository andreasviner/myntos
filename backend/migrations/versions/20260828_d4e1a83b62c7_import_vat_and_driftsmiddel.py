"""Give existing companies the import-VAT codes and the driftsmiddel account.

Both are seeded per company at creation (``_seed_chart_of_accounts`` and
``_seed_tax_codes``), so adding them to the catalogue only reaches companies
created afterwards. Every set of books that already exists would offer the new
options in the interface and then fail on the account lookup when one was
chosen, which is a worse outcome than not offering them.

Rates are stamped from the year the company's books start rather than from
today, matching what the seeding does and for the same reason: these are the
current rates, and claiming they applied earlier would be a fabrication that
every historical posting then snapshots.

**Written against Core tables rather than raw SQL strings.** A data migration
binds values, and SQLite will accept a string where a date, a numeric or an
enum belongs while asyncpg refuses all three. Declaring the columns with their
real types lets SQLAlchemy coerce per dialect, which is the difference between
a migration that passes a SQLite suite and one that also runs on the PostgreSQL
it is actually for.

Nothing is removed on downgrade beyond what this added, and a company that has
already posted with one of these codes keeps its postings: the codes and the
account are deleted only where no line references them.

Revision ID: d4e1a83b62c7
Revises: b7c2e94f10a3
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "d4e1a83b62c7"
down_revision: str | None = "b7c2e94f10a3"
branch_labels: None = None
depends_on: None = None

ACCOUNT_NUMBER = "1250"
ACCOUNT_DESCRIPTION = "Inventar og utstyr"

# (code, description, standard_tax_code, rate)
TAX_CODES: tuple[tuple[str, str, str, str], ...] = (
    ("81", "Innførsel av varer, høy sats (25 %), med fradragsrett", "81", "0.250000"),
    ("82", "Innførsel av varer, høy sats (25 %), uten fradragsrett", "82", "0.250000"),
    ("83", "Innførsel av varer, middels sats (15 %), med fradragsrett", "83", "0.150000"),
    ("86", "Tjenester kjøpt fra utlandet, høy sats (25 %), med fradragsrett", "86", "0.250000"),
    ("87", "Tjenester kjøpt fra utlandet, høy sats (25 %), uten fradragsrett", "87", "0.250000"),
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
tax_code = sa.table(
    "tax_code",
    sa.column("id", sa.Integer),
    sa.column("company_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("description", sa.String),
    sa.column("standard_tax_code", sa.String),
    sa.column("is_reportable", sa.Boolean),
)
tax_rate = sa.table(
    "tax_rate",
    sa.column("tax_code_id", sa.Integer),
    sa.column("valid_from", sa.Date),
    sa.column("rate", sa.Numeric(9, 6)),
)
gl_transaction_line = sa.table(
    "gl_transaction_line",
    sa.column("account_id", sa.Integer),
    sa.column("tax_code_id", sa.Integer),
)


def upgrade() -> None:
    bind = op.get_bind()
    for (company_id,) in bind.execute(sa.select(company.c.id)).fetchall():
        # The year the books start, so created_date and valid_from land where
        # the seeding would have put them.
        start = bind.execute(
            sa.select(sa.func.min(financial_year.c.year)).where(
                financial_year.c.company_id == company_id
            )
        ).scalar()
        if start is None:
            continue
        opens = date(int(start), 1, 1)

        present = bind.execute(
            sa.select(account.c.id).where(
                account.c.company_id == company_id,
                account.c.account_number == ACCOUNT_NUMBER,
            )
        ).scalar()
        if present is None:
            bind.execute(
                account.insert().values(
                    company_id=company_id,
                    account_number=ACCOUNT_NUMBER,
                    description=ACCOUNT_DESCRIPTION,
                    account_type="eiendel",
                    created_date=opens,
                    is_active=True,
                )
            )
            account_id = bind.execute(
                sa.select(account.c.id).where(
                    account.c.company_id == company_id,
                    account.c.account_number == ACCOUNT_NUMBER,
                )
            ).scalar()
            # _open_financial_year gives every account a grouping row per year,
            # so one added afterwards needs them for the years already open, or
            # the SAF-T export has an account it cannot place. "NA" is what
            # SAF-T prescribes for a balance-sheet account.
            years = bind.execute(
                sa.select(financial_year.c.year).where(financial_year.c.company_id == company_id)
            ).fetchall()
            for (year,) in years:
                bind.execute(
                    account_grouping.insert().values(
                        account_id=account_id,
                        financial_year=year,
                        grouping_category="NA",
                        grouping_code="NA",
                    )
                )

        for code, description, standard, rate in TAX_CODES:
            exists = bind.execute(
                sa.select(tax_code.c.id).where(
                    tax_code.c.company_id == company_id, tax_code.c.code == code
                )
            ).scalar()
            if exists is not None:
                continue
            bind.execute(
                tax_code.insert().values(
                    company_id=company_id,
                    code=code,
                    description=description,
                    standard_tax_code=standard,
                    is_reportable=True,
                )
            )
            code_id = bind.execute(
                sa.select(tax_code.c.id).where(
                    tax_code.c.company_id == company_id, tax_code.c.code == code
                )
            ).scalar()
            bind.execute(
                tax_rate.insert().values(tax_code_id=code_id, valid_from=opens, rate=Decimal(rate))
            )


def downgrade() -> None:
    bind = op.get_bind()
    codes = [code for code, *_ in TAX_CODES]
    # Only where nothing was posted with them. A voucher that used one of these
    # is history, and its code has to keep existing for the line to be readable.
    used_codes = sa.select(gl_transaction_line.c.tax_code_id).where(
        gl_transaction_line.c.tax_code_id.isnot(None)
    )
    doomed = sa.select(tax_code.c.id).where(
        tax_code.c.code.in_(codes), tax_code.c.id.notin_(used_codes)
    )
    bind.execute(tax_rate.delete().where(tax_rate.c.tax_code_id.in_(doomed)))
    bind.execute(tax_code.delete().where(tax_code.c.id.in_(doomed)))

    unused = sa.select(account.c.id).where(
        account.c.account_number == ACCOUNT_NUMBER,
        account.c.id.notin_(sa.select(gl_transaction_line.c.account_id)),
    )
    bind.execute(account_grouping.delete().where(account_grouping.c.account_id.in_(unused)))
    bind.execute(account.delete().where(account.c.id.in_(unused)))
