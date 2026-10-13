"""Two VAT returns in one company, and terms filed from another program.

Three tables and a column, all new, for docs/integrations/mva.md «Two returns»
and «Filed elsewhere»:

``mva_second_naering``
    At most one per company: the second MVA-melding of a farm (or forest, or
    fishery) that has other business above 30 000 kroner beside it
    (merverdiavgiftsloven § 15-1 (1), Merverdiavgiftshåndboken 15-1.3). Its
    term length and the day it starts. Its category is not stored: it is
    always the other of the two Myntos files, so storing it would be a second
    copy of the company's own ``vat_category`` that could disagree with it.

``mva_second_naering_account``
    The accounts whose VAT goes in that second melding from its first day.
    Unique on the account, since an account belongs to one melding.

``mva_filing_elsewhere``
    The detail of an ``mva_filing`` row with status ``annet_program``: a
    person's statement that the previous program filed the term, the day and
    (optionally) the amount, the imported settlement voucher it was proposed
    from, and who took it back when it is withdrawn (status ``opphevet``).

``agent_approval.filing_category``
    Which melding a key's request to file is about, when the company files two.

**What it infers:** nothing. Every company keeps the one melding it has
(``company.vat_category`` and ``company.vat_period_type`` are untouched), no
term is marked as filed elsewhere, and every waiting request to file keeps
meaning the main melding (NULL).

**What it refuses to guess:** which accounts belong to a second business and
which terms an earlier program filed. A company's chart does not say the first
(the farm and the snow-ploughing beside it buy the same diesel), and the second
is a statement only the person can make: the imported settlement vouchers are
proposed on the MVA page, never recorded by a migration.

Revision ID: 6c1f9e3a7d52
Revises: a8d3f61c2b97
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c1f9e3a7d52"
down_revision: str | None = "a8d3f61c2b97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(38, 2)


def upgrade() -> None:
    op.create_table(
        "mva_second_naering",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(length=32), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_mva_second_naering_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mva_second_naering"),
        sa.UniqueConstraint("company_id", name="uq_mva_second_naering_company_id"),
    )

    op.create_table(
        "mva_second_naering_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_mva_second_naering_account_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name="fk_mva_second_naering_account_account_id_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mva_second_naering_account"),
        sa.UniqueConstraint("account_id", name="uq_mva_second_naering_account_account_id"),
    )

    op.create_table(
        "mva_filing_elsewhere",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("filed_on", sa.Date(), nullable=False),
        sa.Column("amount", MONEY, nullable=True),
        sa.Column("voucher_pk", sa.Integer(), nullable=True),
        sa.Column("created_via_agent_key_id", sa.Integer(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.Integer(), nullable=True),
        sa.Column("withdrawn_via_agent_key_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["mva_filing.id"],
            name="fk_mva_filing_elsewhere_filing_id_mva_filing",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voucher_pk"],
            ["gl_transaction.id"],
            name="fk_mva_filing_elsewhere_voucher_pk_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_via_agent_key_id"],
            ["agent_key.id"],
            name="fk_mva_filing_elsewhere_created_via_agent_key_id_agent_key",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"],
            ["app_user.id"],
            name="fk_mva_filing_elsewhere_withdrawn_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_via_agent_key_id"],
            ["agent_key.id"],
            name="fk_mva_filing_elsewhere_withdrawn_via_agent_key_id_agent_key",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mva_filing_elsewhere"),
        sa.UniqueConstraint("filing_id", name="uq_mva_filing_elsewhere_filing_id"),
    )

    op.add_column(
        "agent_approval", sa.Column("filing_category", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_approval") as batch:
        batch.drop_column("filing_category")
    op.drop_table("mva_filing_elsewhere")
    op.drop_table("mva_second_naering_account")
    op.drop_table("mva_second_naering")
