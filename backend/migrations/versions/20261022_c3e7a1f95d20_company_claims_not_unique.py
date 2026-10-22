"""More than one set of books may confirm the same orgnr, after a warning.

``uq_company_verified_orgnr`` allowed one confirmed claim per orgnr across the
installation, so the second account to confirm a company was refused with
``company_already_claimed`` even when Altinn said, through that person's own
ID-porten login, that they may act for it. The owner decided that a person
Altinn vouches for must not be stopped by somebody else's books: they are
warned that the company is already registered and asked whether to continue,
and «Ja» confirms (``even_if_claimed`` on ``POST /companies/{id}/bekreft``).

The unique index becomes a plain one, ``ix_company_verified_orgnr``, with the
same condition, because the warning and the «Registrert av en annen» mark on
the Altinn list still ask exactly that question.

**What it infers:** nothing. Every row the unique index allowed, the plain one
allows.

**What it refuses to guess:** on the way down, which of two confirmed claims
on one orgnr should lose its confirmation. The downgrade fails while any
orgnr has more than one, and the person running it decides.

On SQLite a table rebuild has lost this index before (``f4a9d2c63b18``), so
the drop tolerates its absence.

Revision ID: c3e7a1f95d20
Revises: 60bbbdaf279d
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e7a1f95d20"
down_revision: str | None = "60bbbdaf279d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE = "uq_company_verified_orgnr"
PLAIN = "ix_company_verified_orgnr"
OPEN_AND_CONFIRMED = "verified_at IS NOT NULL AND closed_at IS NULL"


def upgrade() -> None:
    op.drop_index(UNIQUE, table_name="company", if_exists=True)
    op.create_index(
        PLAIN,
        "company",
        ["orgnr"],
        postgresql_where=sa.text(OPEN_AND_CONFIRMED),
        sqlite_where=sa.text(OPEN_AND_CONFIRMED),
    )


def downgrade() -> None:
    op.drop_index(PLAIN, table_name="company", if_exists=True)
    op.create_index(
        UNIQUE,
        "company",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text(OPEN_AND_CONFIRMED),
        sqlite_where=sa.text(OPEN_AND_CONFIRMED),
    )
