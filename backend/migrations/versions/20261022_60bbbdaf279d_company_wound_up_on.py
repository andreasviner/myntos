"""The last day of a wound-up company's books: ``company.wound_up_on``.

For :mod:`myntos.domain.arsoppgjor.winding_up`. When an aksjeselskap is
dissolved (oppløst), its last financial year ends on the day its books end, not
on 31 December, and that year's skattemelding is filed early, as a
forhåndsfastsetting (skatteforvaltningsloven § 8-2). The date is the person's
to state; the column is where it is kept, and the final ``financial_year``
row's ``end_date`` is moved to it in the same step.

**What it infers:** nothing. Every company is still in business until someone
says otherwise, so the column starts NULL everywhere.

**What it refuses to guess:** that a company Enhetsregisteret marks as deleted
or under avvikling has ended its books, or on which day. The register's
``slettedato`` is when Brønnøysund removed it, which is often weeks after the
last money left the company; the books end where the person says they do.

Revision ID: 60bbbdaf279d
Revises: b7e3d91c4a06
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "60bbbdaf279d"
down_revision: str | None = "b7e3d91c4a06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("company", sa.Column("wound_up_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "wound_up_on")
