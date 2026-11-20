"""Sending an EHF over Peppol: ``peppol_registration`` and ``peppol_transmission``.

For ``myntos.domain.peppol`` and the access point in
``myntos.integrations.recommand`` (docs/integrations/peppol.md). A registration
is a company as the access point knows it; a transmission is one send of one
issued invoice or credit note and what the access point said about it.

Neither table points at a person (docs/gdpr.md has nothing to do here).
``uq_peppol_transmission_live`` lets a document have one transmission that has
not failed, so an invoice that went cannot go twice and one that failed can be
tried again.

**What it infers:** nothing. Both tables start empty.

**What it refuses to guess:** that an invoice issued before this revision was
sent. Every issued document starts with no transmission, including those whose
XML the seller downloaded and sent some other way; Myntos never saw those go, and
a row saying they did would be a record of something nobody recorded.

Revision ID: 8d7b599c0754
Revises: 5e8b3c1f7a92
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d7b599c0754"
down_revision: str | None = "5e8b3c1f7a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOT_FAILED = "status != 'failed'"


def upgrade() -> None:
    op.create_table(
        "peppol_registration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("remote_company_id", sa.String(length=64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_peppol_registration_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_peppol_registration"),
        sa.UniqueConstraint("company_id", name="uq_peppol_registration_company_id"),
    )
    op.create_table(
        "peppol_transmission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("document_pk", sa.Integer(), nullable=False),
        sa.Column("remote_document_id", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "delivered",
                "failed",
                name="peppol_transmission_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("failure_category", sa.String(length=32), nullable=True),
        sa.Column("failure_message", sa.String(length=1000), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_peppol_transmission_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_pk"],
            ["sales_document.id"],
            name="fk_peppol_transmission_document_pk_sales_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_peppol_transmission"),
    )
    op.create_index(
        "uq_peppol_transmission_live",
        "peppol_transmission",
        ["document_pk"],
        unique=True,
        postgresql_where=sa.text(NOT_FAILED),
        sqlite_where=sa.text(NOT_FAILED),
    )


def downgrade() -> None:
    # Loses the record of which invoices went out over Peppol. Nothing else
    # holds it on Myntos's side; the access point still does.
    op.drop_index("uq_peppol_transmission_live", table_name="peppol_transmission")
    op.drop_table("peppol_transmission")
    op.drop_table("peppol_registration")
