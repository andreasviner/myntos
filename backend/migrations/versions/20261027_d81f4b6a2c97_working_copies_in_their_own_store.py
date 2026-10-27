"""Uploaded SAF-T files and printouts are kept in a store of their own, not in a column.

``saft_import_file.content`` held each uploaded file in the database. A person
moving from Fiken uploads its complete export, 350 to 600 MB (2026-10-27), and
in Postgres that is a value near the 1 GB ceiling, storage billed together with
the restore history of every row written, and the file twice through the
process's memory. The files now go to ``myntos.storage.working`` (an R2 bucket
of their own in production), and the row keeps the key: ``storage_key``.

**What it infers:** nothing. Rows uploaded before this keep their ``content``
and are read from it as before; the 14-day rule empties them, and ``content``
goes in a later migration once none is left. Nothing is copied out of the
database here: a migration that wrote to R2 would be a deployment that needs the
network to succeed, for files that are gone within two weeks anyway.

**What it refuses to guess:** on the way down, how to bring a file kept in the
store back into the database. The column goes, those imports no longer have
their files, and each says so («Last dem opp på nytt») instead of being read
from a copy the older code cannot find. The objects are left to the bucket's
lifecycle rule.

Revision ID: d81f4b6a2c97
Revises: c3e7a1f95d20
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d81f4b6a2c97"
down_revision: str | None = "c3e7a1f95d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("saft_import_file", sa.Column("storage_key", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("saft_import_file") as batch:
        batch.drop_column("storage_key")
