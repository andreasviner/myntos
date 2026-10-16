"""A ledger account other than 1920 and 1900 can be the company's bank account.

One column on ``account``, ``is_bank_account``. The overview counts money in
and out, and the bank balance, on 1920 and 1900; an account with this set
counts alongside them (``myntos.domain.ledger.kontoplan.counts_as_bank``).
Tripletex keeps a farm's only bank account, a driftskreditt, on 2390, and the
farm's overview read «Inn i år 0,00» for a year of bank movements. The
vouchers stay where the previous program put them; Myntos learns where the
bank is.

**What it infers:** nothing. Every existing account starts unmarked, so every
company's overview counts exactly what it counted before. 1920 and 1900 are
not marked either: they count by number in the code and need no row to say so.

**What it refuses to guess:** which accounts are bank accounts. An account
whose name carries one of the company's kontonumre (``company_bank_account``)
looks like one, and a SAF-T import marks such an account when the *file's own
header* names the number, but a name typed into a kontoplan is not that
evidence, and marking the wrong account would put a loan or a deposit into the
figures the person reads first. The farm already in production is marked by a
person on the Kontoplan page, or by bringing its files in again.

Revision ID: f3a9c6e2b814
Revises: e8b1c5d07a42
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c6e2b814"
down_revision: str | None = "e8b1c5d07a42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "is_bank_account"


def upgrade() -> None:
    # The server default backfills the existing rows and is then dropped, as
    # for the other booleans: the model's Python-side default is the one rule.
    op.add_column(
        "account", sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false())
    )
    with op.batch_alter_table("account") as batch:
        batch.alter_column(COLUMN, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("account") as batch:
        batch.drop_column(COLUMN)
