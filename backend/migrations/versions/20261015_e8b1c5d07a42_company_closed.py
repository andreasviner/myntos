"""A company can be deleted from Myntos, and its books are kept until the law lets them go.

Two columns on ``company`` for ``myntos.domain.identity.closure``
(docs/gdpr.md, «Deleting a company»):

``closed_at``
    When an owner deleted the company. From then on nobody works in it, it is
    in no list, and no key or invitation leads to it.
``retain_until``
    The last day the books are kept: 31 December five years after the last
    financial year anything was entered in (bokføringsloven § 13 second
    paragraph). The day after, the purge deletes them for good.

``uq_company_verified_orgnr`` is rebuilt to bind only open companies. Books
deleted from Myntos must not hold a confirmed claim on the orgnr for five
years: whoever runs that company next, through another account or the same
one, has to be able to confirm it.

**What it infers:** nothing. Every existing company is open, so both columns
start NULL, and the rebuilt index binds exactly the rows it bound before.

**What it refuses to guess:** a retention date for anything. It is worked out
when an owner deletes the company, from the vouchers as they stand then, and
never backfilled.

No column for who deleted it. The company's owners are the only people who
can, it can be undone until the purge, and an actor column would be one more
place the account erasure has to keep a name.

Revision ID: e8b1c5d07a42
Revises: d2f6a8c41e57
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b1c5d07a42"
down_revision: str | None = "d2f6a8c41e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_company_verified_orgnr"
OPEN_AND_CONFIRMED = "verified_at IS NOT NULL AND closed_at IS NULL"
CONFIRMED = "verified_at IS NOT NULL"


def upgrade() -> None:
    op.add_column("company", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("company", sa.Column("retain_until", sa.Date(), nullable=True))
    # if_exists: on SQLite a later table rebuild of ``company`` lost the index,
    # so a database migrated there from scratch has none to drop. PostgreSQL
    # has it. Either way the line below puts the right one in place.
    op.drop_index(INDEX, table_name="company", if_exists=True)
    op.create_index(
        INDEX,
        "company",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text(OPEN_AND_CONFIRMED),
        sqlite_where=sa.text(OPEN_AND_CONFIRMED),
    )


def downgrade() -> None:
    # Fails if a deleted company and an open one both hold a confirmed claim on
    # the same orgnr, which is the right outcome: the old index cannot say
    # which of the two should keep it, and neither can this migration.
    op.drop_index(INDEX, table_name="company", if_exists=True)
    op.create_index(
        INDEX,
        "company",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text(CONFIRMED),
        sqlite_where=sa.text(CONFIRMED),
    )
    with op.batch_alter_table("company") as batch:
        batch.drop_column("retain_until")
        batch.drop_column("closed_at")
