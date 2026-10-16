"""Deleted books no longer stop the same orgnr being set up again in the account.

``uq_company_organization_id`` made an account keep one set of books per
orgnr, whatever state they were in. Once deleting a company locked its books
for five years instead of removing them (``e8b1c5d07a42``), that meant a person
who deleted a company to start over, or who had set one up with the wrong
number while trying Myntos, could not set it up again for five years. It was
reported exactly so: a test company with a made-up orgnr, deleted, and
«regnskapet for … er slettet, men oppbevares fortsatt» on the next attempt.

It becomes a partial unique index, ``uq_company_open_orgnr``, that binds open
books only. The deleted ones stay locked for their retention beside the new
ones; opening them again while new books for the same orgnr are open is
refused by ``closure.reopen_company`` instead.

**What it infers:** nothing. Every pair the old constraint allowed, the index
allows.

**What it refuses to guess:** whether a deleted company that shares its orgnr
with new books is the same business. It might be, or the number was typed
wrong once; the deleted books are kept either way.

On SQLite dropping a constraint rebuilds the table, and a rebuild does not
carry a partial index's WHERE clause across reliably (which is how
``uq_company_verified_orgnr`` once went missing there), so that index is put
back explicitly afterwards. PostgreSQL drops the constraint in place.

Revision ID: f4a9d2c63b18
Revises: f3a9c6e2b814
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a9d2c63b18"
down_revision: str | None = "f3a9c6e2b814"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "uq_company_organization_id"
OPEN_ORGNR = "uq_company_open_orgnr"
VERIFIED = "uq_company_verified_orgnr"
OPEN = "closed_at IS NULL"
OPEN_AND_CONFIRMED = "verified_at IS NOT NULL AND closed_at IS NULL"


def _sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _restore_verified_index() -> None:
    op.drop_index(VERIFIED, table_name="company", if_exists=True)
    op.create_index(
        VERIFIED,
        "company",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text(OPEN_AND_CONFIRMED),
        sqlite_where=sa.text(OPEN_AND_CONFIRMED),
    )


def upgrade() -> None:
    if _sqlite():
        with op.batch_alter_table("company", recreate="always") as batch:
            batch.drop_constraint(CONSTRAINT, type_="unique")
        _restore_verified_index()
    else:
        op.drop_constraint(CONSTRAINT, "company", type_="unique")
    op.create_index(
        OPEN_ORGNR,
        "company",
        ["organization_id", "orgnr"],
        unique=True,
        postgresql_where=sa.text(OPEN),
        sqlite_where=sa.text(OPEN),
    )


def downgrade() -> None:
    # Fails while an account holds deleted and open books for the same orgnr,
    # which is the right outcome: the old constraint cannot hold both, and
    # which to give up is not this migration's call.
    op.drop_index(OPEN_ORGNR, table_name="company")
    if _sqlite():
        with op.batch_alter_table("company", recreate="always") as batch:
            batch.create_unique_constraint(CONSTRAINT, ["organization_id", "orgnr"])
        _restore_verified_index()
    else:
        op.create_unique_constraint(CONSTRAINT, "company", ["organization_id", "orgnr"])
