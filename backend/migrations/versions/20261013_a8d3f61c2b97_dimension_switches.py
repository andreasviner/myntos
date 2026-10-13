"""Dimensions a person can use: a switch on each, and an index to report by.

The tables for SAF-T's analysis dimensions (``dimension_type``,
``dimension_value``, ``gl_line_analysis``) have existed since the accounting
core, with nothing writing to them. Avdelinger, prosjekter and a farm's
driftsgrener are now made, renamed and switched off by people
(``myntos.domain.ledger.dimensions``), and a register line can carry them.

Two changes:

``is_active`` on ``dimension_type`` and on ``dimension_value``. Switched off,
a dimension or a value is offered for nothing new and stays on every line that
used it, as an account does. A value in use is never deleted.

``ix_gl_line_analysis_dimension_value_id``, because the report per avdeling
reads the allocations by value, and so does the question whether a value is in
use. The unique constraint on the table leads with the line and helps neither.

**What it infers:** that every existing dimension and value is active, since
nothing could switch one off before this revision. There are none in a
database that has only ever been written by Myntos; the backfill is for one
that someone filled by hand.

**What it refuses to guess:** nothing else. No posted line is touched, and no
allocation is inferred for a line that has none: a line's avdeling is what the
person or the old program said it was, and a migration saying it later would be
a migration editing posted vouchers.

Revision ID: a8d3f61c2b97
Revises: e4b7c2a9d815
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8d3f61c2b97"
down_revision: str | None = "e4b7c2a9d815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SWITCH = "is_active"
INDEX = "ix_gl_line_analysis_dimension_value_id"


def upgrade() -> None:
    # Backfilled on through a server default that is dropped again at once: the
    # model declares the default Python-side, and one left in the database is a
    # phantom diff on every autogenerate (9d2b6f4e1a37 does the same).
    for table in ("dimension_type", "dimension_value"):
        op.add_column(
            table, sa.Column(SWITCH, sa.Boolean(), nullable=False, server_default=sa.true())
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column(SWITCH, server_default=None)
    op.create_index(INDEX, "gl_line_analysis", ["dimension_value_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="gl_line_analysis")
    for table in ("dimension_value", "dimension_type"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(SWITCH)
