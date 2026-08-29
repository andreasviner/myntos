"""Recover the correction link for edits made before it existed.

``f3a5c81d90e4`` added ``corrects_transaction_pk``, so every edit from that
point on records what it replaced. Edits made earlier did not, and their three
vouchers stay unreadable as one correction forever unless the link is
reconstructed. In a register that folds corrections into a single line, that is
the difference between books that read correctly and books where one purchase
is listed three times and its reversal shows up as income.

**What this infers, precisely.** ``edit_entry`` is the only code path in the
application that posts a non-reversal in the same database transaction as a
reversal. ``system_entry_date`` defaults to ``now()``, which PostgreSQL holds
constant for the whole transaction, so the reversal and the replacement carry
the identical timestamp; two vouchers from separate requests do not. The
replacement is also inserted immediately after the reversal, so it is the very
next voucher in that journal.

Both conditions are required, and adjacency is what makes this safe on SQLite,
where CURRENT_TIMESTAMP only resolves to the second and two unrelated requests
could otherwise share a timestamp. A pair that fails either test is left alone:
missing a link costs a folded row, and the register falls back to grouping the
entry with its reversal. Inventing one would claim a correction that never
happened.

Nothing here changes an amount, an account or a posting. The column is how the
register reads the ledger back, not part of what the ledger says.

Revision ID: a91b6d02e7c5
Revises: f3a5c81d90e4
"""

from __future__ import annotations

from itertools import pairwise

import sqlalchemy as sa
from alembic import op

revision: str = "a91b6d02e7c5"
down_revision: str | None = "f3a5c81d90e4"
branch_labels: None = None
depends_on: None = None

transaction = sa.table(
    "gl_transaction",
    sa.column("id", sa.Integer),
    sa.column("journal_id", sa.Integer),
    sa.column("system_entry_date", sa.DateTime(timezone=True)),
    sa.column("reverses_transaction_pk", sa.Integer),
    sa.column("corrects_transaction_pk", sa.Integer),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                transaction.c.id,
                transaction.c.journal_id,
                transaction.c.system_entry_date,
                transaction.c.reverses_transaction_pk,
                transaction.c.corrects_transaction_pk,
            ).order_by(transaction.c.journal_id, transaction.c.id)
        )
    )

    for previous, following in pairwise(rows):
        _, journal, stamped, reverses, _ = previous
        next_pk, next_journal, next_stamped, next_reverses, next_corrects = following
        if reverses is None:
            # Not a reversal, so nothing was being corrected here.
            continue
        if next_journal != journal:
            # The reversal is the last voucher in its journal; whatever follows
            # belongs to another set of books.
            continue
        if next_reverses is not None or next_corrects is not None:
            # Already a reversal itself, or already linked.
            continue
        if stamped is None or next_stamped is None or stamped != next_stamped:
            # Different database transactions, so not one correction. This is
            # the condition that does the real work; adjacency alone would pair
            # a reversal with whatever happened to be posted next.
            continue
        bind.execute(
            transaction.update()
            .where(transaction.c.id == next_pk)
            .values(corrects_transaction_pk=reverses)
        )


def downgrade() -> None:
    # Only what this migration could have written. A link set by ``edit_entry``
    # after the column existed is a fact rather than an inference, and clearing
    # it would lose something that cannot be recovered a second time. There is
    # no way to tell the two apart afterwards, so nothing is cleared: the
    # column itself goes away with ``f3a5c81d90e4``, which is the revision that
    # owns it.
    pass
