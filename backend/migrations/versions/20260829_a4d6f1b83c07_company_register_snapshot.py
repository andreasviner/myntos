"""Keep Enhetsregisteret's whole answer, not only the fields we read by name.

``company`` already caches the handful the system uses — the næringskode a
næringsspesifikasjon asks for, the address and registration flags an invoice
repeats. Everything else the register holds was fetched, shown once and thrown
away: styre and revisor, aksjekapital, vedtektsfestet formål, stiftelsesdato,
tidligere navn. Wanting any of it later meant another round trip to Brønnøysund
for something we had already been told.

JSON rather than a column per field, deliberately. Nothing in here is queried by
field, the register adds fields without asking anybody, and a column apiece
would turn each of those additions into a migration. What *is* queried — which
company, which orgnr, and how old the answer is — gets a column. JSONB on
PostgreSQL, because a ``json`` column there reparses the document on every read
for no benefit; plain JSON on SQLite, which has no such distinction.

Its own table, not more columns on ``company``. Every page load lists companies,
and none of those listings wants a document-sized blob dragged along.

``orgnr`` is stored beside ``company_id`` because an unconfirmed company's
number can still be corrected. A snapshot taken under the previous number is not
a stale answer about this company — it is an accurate answer about a different
one, and the read path discards it rather than showing somebody else's board.

Nothing is backfilled. A backfill is one HTTP request per company inside a
migration, and the next refresh fills each row in anyway.

Revision ID: a4d6f1b83c07
Revises: e5b2a94c37d1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d6f1b83c07"
down_revision: str | None = "e5b2a94c37d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "company_register_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("unit", _json(), nullable=False),
        # Not nullable, and an empty list is a real answer: a sole trader has no
        # registered roles at all, and that is different from never having asked.
        # "Never asked" is the absence of the row.
        sa.Column("roles", _json(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_company_register_snapshot_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_register_snapshot"),
        # One row per company: this is the current picture, and a superseded
        # register answer describes our fetching rather than the business.
        sa.UniqueConstraint("company_id", name="uq_company_register_snapshot_company_id"),
    )


def downgrade() -> None:
    op.drop_table("company_register_snapshot")
