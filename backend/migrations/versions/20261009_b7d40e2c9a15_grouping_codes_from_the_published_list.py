"""Næringsspesifikasjon groupings checked against Skatteetaten's published list.

The seeded chart's ``GroupingCategory``/``GroupingCode`` pairs were written from
the documented *shape* of the code list, without the list. Checked against
"Grouping Category Code 2025-2026" in ``github.com/Skatteetaten/saf-t``, half
of them did not exist:

- the payroll category is ``loennskostnad``, not ``lonnskostnad``;
- 4000 Varekjøp belongs on 4005, there is no post 4000;
- 6800, 6810, 6900, 7140, 7320, 7770 and 7790 named posts the list does not
  have;
- 8060 and 8160 were folded into the interest posts although currency gains
  and losses have posts of their own;
- every balance-sheet account said ``NA``, which the list reserves for accounts
  with genuinely no post, while it has one for each of ours.

A SAF-T file carrying those pairs passes the XSD, because the XSD only says the
category is a string. It fails at the auditor, who reads the file by category.

**What it infers:** that a row still holding exactly the pair the old seed
wrote was written by the seed and never chosen by a person. Nothing in Myntos
lets a person edit a grouping, so today that is every row. Every year's row is
corrected, not only the current year's: the old pairs were never right for any
year, and the published list has not changed these posts between 2024 and 2026.

**That inference held on the day this ran and holds nowhere after it.** From
``c5a2e7d94b13`` on, the kontoplan page lets a person choose any listed pair,
and a person's choice can coincide with a seed's. Do not copy this migration's
match-on-the-old-pair rule into a later one: a later correction of seeded rows
must also require ``chosen_at IS NULL``, which that revision added for exactly
this purpose. Nothing after this revision relies on the old inference.

**What it refuses to guess:** a row holding anything else. It is left exactly
as it is, and the SAF-T check will name it if it is not in the list.

Written against Core tables for the reason ``d4e1a83b62c7`` gives.

Revision ID: b7d40e2c9a15
Revises: e1cb264f0c44
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b7d40e2c9a15"
down_revision: str | None = "e1cb264f0c44"
branch_labels: None = None
depends_on: None = None

# account number -> (old pair, new pair). Copied rather than imported from
# myntos.domain.ledger.chart: a migration must keep meaning what it meant on the
# day it was written, and the chart will move on.
CORRECTIONS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "1250": (("NA", "NA"), ("balanseverdiForAnleggsmiddel", "1205")),
    "1500": (("NA", "NA"), ("balanseverdiForOmloepsmiddel", "1500")),
    "1900": (("NA", "NA"), ("balanseverdiForOmloepsmiddel", "1900")),
    "1920": (("NA", "NA"), ("balanseverdiForOmloepsmiddel", "1920")),
    "2050": (("NA", "NA"), ("egenkapital", "2050")),
    "2060": (("NA", "NA"), ("egenkapital", "2050")),
    "2220": (("NA", "NA"), ("langsiktigGjeld", "2220")),
    "2400": (("NA", "NA"), ("kortsiktigGjeld", "2400")),
    "2600": (("NA", "NA"), ("kortsiktigGjeld", "2600")),
    "2700": (("NA", "NA"), ("kortsiktigGjeld", "2740")),
    "2710": (("NA", "NA"), ("kortsiktigGjeld", "2740")),
    "2740": (("NA", "NA"), ("kortsiktigGjeld", "2740")),
    "4000": (("varekostnad", "4000"), ("varekostnad", "4005")),
    "5000": (("lonnskostnad", "5000"), ("loennskostnad", "5000")),
    "5400": (("lonnskostnad", "5400"), ("loennskostnad", "5400")),
    "6800": (("annenDriftskostnad", "6800"), ("annenDriftskostnad", "6995")),
    "6810": (("annenDriftskostnad", "6800"), ("annenDriftskostnad", "6350")),
    "6900": (("annenDriftskostnad", "6900"), ("annenDriftskostnad", "6995")),
    "7140": (("annenDriftskostnad", "7140"), ("annenDriftskostnad", "7165")),
    "7320": (("annenDriftskostnad", "7320"), ("annenDriftskostnad", "7330")),
    "7770": (("annenDriftskostnad", "7770"), ("annenDriftskostnad", "7700")),
    "7790": (("annenDriftskostnad", "7790"), ("annenDriftskostnad", "7700")),
    "8060": (("finansinntekt", "8050"), ("finansinntekt", "8060")),
    "8160": (("finanskostnad", "8150"), ("finanskostnad", "8160")),
}

metadata = sa.MetaData()
account = sa.Table(
    "account",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("account_number", sa.String(32)),
)
account_grouping = sa.Table(
    "account_grouping",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("account_id", sa.Integer),
    sa.Column("grouping_category", sa.String(64)),
    sa.Column("grouping_code", sa.String(64)),
)


def _rewrite(direction: int) -> None:
    bind = op.get_bind()
    for number, pairs in CORRECTIONS.items():
        (old_category, old_code), (new_category, new_code) = pairs[::direction]
        accounts = sa.select(account.c.id).where(account.c.account_number == number)
        bind.execute(
            account_grouping.update()
            .where(
                account_grouping.c.account_id.in_(accounts),
                account_grouping.c.grouping_category == old_category,
                account_grouping.c.grouping_code == old_code,
            )
            .values(grouping_category=new_category, grouping_code=new_code)
        )


def upgrade() -> None:
    _rewrite(1)


def downgrade() -> None:
    # Puts the old pairs back on exactly the rows that hold the new ones. Not
    # quite the inverse for 2060, which shares its new pair with 2050 — but the
    # account number is part of the match, so each row still goes back to what
    # its own account had.
    _rewrite(-1)
