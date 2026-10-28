"""What a company lets Myntos use to improve Myntos: ``company.data_sharing``.

Two changes, for docs/data-sharing.md.

``company.data_sharing`` is the level in force: ``none``, ``usage``,
``anonymised`` or ``everything``, chosen by an owner or admin on
``/organisasjon/``.

``data_sharing_choice`` is the log of every change, by whom and when, because
the level alone cannot say whether the company had agreed on the day something
was taken.

**What it infers:** that no company has agreed to share anything, so every
existing row is backfilled ``none``. That is not a guess: nothing could be
chosen before this revision. In particular, ``ki_enabled`` is not read as a
yes. It lets Myntos send a bilag to a model to answer the company's own
question, and says nothing about Myntos keeping it for itself; silence is not
consent, and neither is a different consent.

**What it refuses to guess:** a log for the rows it backfills. The log starts
empty, and an empty log with ``none`` means exactly "nobody has chosen". A row
written now would claim a decision that was never made, by nobody.

Revision ID: 5e8b3c1f7a92
Revises: 9c4f2e7a1b35
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e8b3c1f7a92"
down_revision: str | None = "9c4f2e7a1b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEVEL = "data_sharing"


def upgrade() -> None:
    # Backfilled through a server default that is dropped again at once, as
    # 9d2b6f4e1a37 does for agent_access_enabled: the model declares the
    # default Python-side, and one left in the database is a phantom diff on
    # every autogenerate.
    op.add_column(
        "company",
        sa.Column(LEVEL, sa.String(length=32), nullable=False, server_default="none"),
    )
    with op.batch_alter_table("company") as batch:
        batch.alter_column(LEVEL, server_default=None)

    op.create_table(
        "data_sharing_choice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column(
            "chosen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("chosen_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_data_sharing_choice_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chosen_by_user_id"],
            ["app_user.id"],
            name="fk_data_sharing_choice_chosen_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_sharing_choice"),
    )
    op.create_index(
        "ix_data_sharing_choice_company_id", "data_sharing_choice", ["company_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_data_sharing_choice_company_id", table_name="data_sharing_choice")
    op.drop_table("data_sharing_choice")
    with op.batch_alter_table("company") as batch:
        batch.drop_column(LEVEL)
