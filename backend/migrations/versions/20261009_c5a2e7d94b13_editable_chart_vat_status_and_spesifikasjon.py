"""An editable kontoplan, a VAT registration date, and the melding's spesifikasjon.

Three columns, each for something the books could not say before.

``company.vat_registered_from``: the day the registration in
Merverdiavgiftsregisteret takes effect. Whether a purchase carries a deduction
depends on the date of the purchase, not on what the flag says when somebody
types it in.

``account_grouping.chosen_at`` / ``chosen_by_user_id``: who chose a
næringsspesifikasjon pair, now that a person can. ``b7d40e2c9a15`` corrected
every row still holding the old seed's exact pair on the inference that
"nothing in Myntos lets a person edit a grouping". From this revision on that
inference is false, and the columns are what replace it: a later migration
correcting seeded groupings must leave ``chosen_at IS NOT NULL`` rows alone.

``gl_transaction_line.vat_specification``: the melding's ``spesifikasjon``
(uttak, tap på krav, justering, tilbakeføring), on the line that carries the tax
information.

**What it infers:** nothing it writes. Every existing grouping row was written by
the seed or by ``b7d40e2c9a15``, because until this revision there was no way to
write one otherwise, so ``chosen_at`` is NULL on all of them and that is a fact
rather than a guess. Every existing ledger line has no specification, because
nothing could record one. An existing company's registration is taken to cover
its whole books (``vat_registered_from`` NULL), which is how every entry so far
has been posted, so reading it that way changes nothing that has happened.

**What it refuses to guess:** a registration date. Enhetsregisteret's open data
says *whether* a unit is in Merverdiavgiftsregisteret and not since when, and
inventing one (the first financial year's start, the company's creation date)
would make the next correction of an old entry refuse a deduction the company
was entitled to. The date stays empty until a person enters it.

Revision ID: c5a2e7d94b13
Revises: c3f9a2d71e58
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "c5a2e7d94b13"
down_revision: str | None = "c3f9a2d71e58"
branch_labels: None = None
depends_on: None = None

CHOSEN_BY_FK = "fk_account_grouping_chosen_by_user_id_app_user"


def _account_grouping() -> sa.Table:
    """``account_grouping`` as ``9f2a1c4b7e30`` created it.

    Handed to batch mode because SQLite cannot add a foreign key to an existing
    table; alembic rebuilds it, and has to be told what it looked like first.
    """
    return sa.Table(
        "account_grouping",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("financial_year", sa.Integer(), nullable=False),
        sa.Column("grouping_category", sa.String(length=64), nullable=False),
        sa.Column("grouping_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_account_grouping_account_id_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_grouping"),
        sa.UniqueConstraint("account_id", "financial_year", name="uq_account_grouping_account_id"),
    )


def upgrade() -> None:
    op.add_column("company", sa.Column("vat_registered_from", sa.Date(), nullable=True))
    op.add_column(
        "gl_transaction_line",
        sa.Column("vat_specification", sa.String(length=64), nullable=True),
    )
    with op.batch_alter_table("account_grouping", copy_from=_account_grouping()) as batch:
        batch.add_column(sa.Column("chosen_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("chosen_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            CHOSEN_BY_FK, "app_user", ["chosen_by_user_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    # No drop_constraint for the foreign key, for the reason f3a5c81d90e4
    # gives: batch mode rebuilds without reflecting it, and dropping the column
    # takes the constraint with it.
    with op.batch_alter_table("account_grouping") as batch:
        batch.drop_column("chosen_by_user_id")
        batch.drop_column("chosen_at")
    with op.batch_alter_table("gl_transaction_line") as batch:
        batch.drop_column("vat_specification")
    with op.batch_alter_table("company") as batch:
        batch.drop_column("vat_registered_from")
