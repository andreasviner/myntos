"""Move KI weekly and period quotas from raw tokens to NOK value budgets.

Paid KI levels («Medium» and «Smart») run on different models with very
different per-token prices on Løynd, so raw token counts cannot bound usage
fairly across levels. Quotas are now tracked in NOK (ex. VAT, micro-krone
precision) internally and exposed to users strictly as remaining percentage.

Schema changes:

- ``ki_subscription``: add ``weekly_value_nok`` (``Numeric(38, 2)``) and drop
  ``weekly_tokens_allowance``.
- ``ki_weekly_usage``: add ``value_used_nok`` (``Numeric(38, 6)``, default 0);
  ``tokens_used`` stays as an internal telemetry counter.
- ``ki_period``: add ``value_allowance_nok`` (``Numeric(38, 2)``) and
  ``value_used_nok`` (``Numeric(38, 6)``, default 0), and drop
  ``tokens_allowance``; ``tokens_used`` stays as internal telemetry.
- ``ki_chat_message``: add ``cost_nok`` (``Numeric(38, 6)``, default 0) as an
  internal cost audit column.

**What it infers:** for any existing ``ki_subscription`` or ``ki_period`` row
with a standard plan code (``start``, ``pluss``, ``pro``), sets
``weekly_value_nok`` to 9.00 / 28.00 / 75.00 NOK and ``value_allowance_nok``
to 36.00 / 112.00 / 300.00 NOK respectively.

**What it refuses to guess:** historical NOK spend from old unweighted
``tokens_used`` counts (those did not record which model or input/output split
ran, so ``value_used_nok`` starts at 0.000000 rather than inventing a cost),
and budgets for unknown/retired plan codes (which stay 0.00).

Revision ID: e7a2b5c91d04
Revises: c9f3a7d21e40
Create Date: 2026-11-28 09:55:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a2b5c91d04"
down_revision: str | Sequence[str] | None = "c9f3a7d21e40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(38, 2)
_VALUE_NOK = sa.Numeric(38, 6)

_WEEKLY_BY_PLAN = (
    ("start", "9.00"),
    ("pluss", "28.00"),
    ("pro", "75.00"),
)
_MONTHLY_BY_PLAN = (
    ("start", "36.00"),
    ("pluss", "112.00"),
    ("pro", "300.00"),
)
_OLD_WEEKLY_TOKENS_BY_PLAN = (
    ("start", 250_000),
    ("pluss", 1_000_000),
    ("pro", 3_000_000),
)
_OLD_MONTHLY_TOKENS_BY_PLAN = (
    ("start", 1_000_000),
    ("pluss", 4_000_000),
    ("pro", 12_000_000),
)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "ki_subscription",
        sa.Column(
            "weekly_value_nok",
            _MONEY,
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    for code, amount in _WEEKLY_BY_PLAN:
        bind.execute(
            sa.text("UPDATE ki_subscription SET weekly_value_nok = :amount WHERE plan = :code"),
            {"amount": amount, "code": code},
        )
    op.drop_column("ki_subscription", "weekly_tokens_allowance")
    if bind.dialect.name == "postgresql":
        op.alter_column("ki_subscription", "weekly_value_nok", server_default=None)

    op.add_column(
        "ki_weekly_usage",
        sa.Column(
            "value_used_nok",
            _VALUE_NOK,
            nullable=False,
            server_default=sa.text("0.000000"),
        ),
    )

    op.add_column(
        "ki_period",
        sa.Column(
            "value_allowance_nok",
            _MONEY,
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column(
        "ki_period",
        sa.Column(
            "value_used_nok",
            _VALUE_NOK,
            nullable=False,
            server_default=sa.text("0.000000"),
        ),
    )
    for code, amount in _MONTHLY_BY_PLAN:
        bind.execute(
            sa.text("UPDATE ki_period SET value_allowance_nok = :amount WHERE plan = :code"),
            {"amount": amount, "code": code},
        )
    op.drop_column("ki_period", "tokens_allowance")
    if bind.dialect.name == "postgresql":
        op.alter_column("ki_period", "value_allowance_nok", server_default=None)

    op.add_column(
        "ki_chat_message",
        sa.Column(
            "cost_nok",
            _VALUE_NOK,
            nullable=False,
            server_default=sa.text("0.000000"),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_column("ki_chat_message", "cost_nok")

    op.add_column(
        "ki_period",
        sa.Column(
            "tokens_allowance",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    for code, tokens in _OLD_MONTHLY_TOKENS_BY_PLAN:
        bind.execute(
            sa.text("UPDATE ki_period SET tokens_allowance = :tokens WHERE plan = :code"),
            {"tokens": tokens, "code": code},
        )
    op.drop_column("ki_period", "value_used_nok")
    op.drop_column("ki_period", "value_allowance_nok")

    op.drop_column("ki_weekly_usage", "value_used_nok")

    op.add_column(
        "ki_subscription",
        sa.Column(
            "weekly_tokens_allowance",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    for code, tokens in _OLD_WEEKLY_TOKENS_BY_PLAN:
        bind.execute(
            sa.text(
                "UPDATE ki_subscription SET weekly_tokens_allowance = :tokens WHERE plan = :code"
            ),
            {"tokens": tokens, "code": code},
        )
    op.drop_column("ki_subscription", "weekly_value_nok")
