"""Peppol access point move to Ademico, plus receiving incoming EHF invoices.

Extends ``peppol_registration`` with Ademico's ``remote_registration_id``, the
denormalised ``orgnr``, ``receiving_enabled`` / ``receiving_enabled_at`` and
the partial unique index ``uq_peppol_registration_receiving_orgnr`` so at most
one active set of books in Myntos receives EHF for a given organisasjonsnummer.
Widens ``peppol_transmission.remote_document_id`` to 128 characters for
Ademico's transmission identifiers. Creates ``peppol_incoming_document`` for
received supplier invoices and credit notes.

**What it infers:** populates ``peppol_registration.orgnr`` from
``company.orgnr`` for any existing registration rows and defaults
``receiving_enabled`` to false.

**What it refuses to guess:** that any company already registered with the
previous access point (Recommand) is registered to receive at Ademico, or has
an Ademico registration id. Existing rows stay send-only with
``remote_registration_id = NULL``; re-registering or enabling receiving adopts
or creates the Ademico legal entity cleanly.

Revision ID: c7e3b91a4f20
Revises: 6ad191291425
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e3b91a4f20"
down_revision: str | None = "6ad191291425"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("peppol_registration") as batch_op:
        batch_op.add_column(
            sa.Column("remote_registration_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("orgnr", sa.String(length=9), nullable=True))
        batch_op.add_column(
            sa.Column(
                "receiving_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("receiving_enabled_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute(
        sa.text(
            "UPDATE peppol_registration "
            "SET orgnr = (SELECT orgnr FROM company "
            "WHERE company.id = peppol_registration.company_id)"
        )
    )

    op.create_index(
        "uq_peppol_registration_receiving_orgnr",
        "peppol_registration",
        ["orgnr"],
        unique=True,
        postgresql_where=sa.text("receiving_enabled IS TRUE AND orgnr IS NOT NULL"),
        sqlite_where=sa.text("receiving_enabled = 1 AND orgnr IS NOT NULL"),
    )

    with op.batch_alter_table("peppol_transmission") as batch_op:
        batch_op.alter_column(
            "remote_document_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=False,
        )

    op.create_table(
        "peppol_incoming_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("transmission_id", sa.String(length=128), nullable=False),
        sa.Column("sbdh_transmission_id", sa.String(length=128), nullable=True),
        sa.Column(
            "document_type",
            sa.Enum(
                "faktura",
                "kreditnota",
                name="peppol_incoming_document_type",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("invoice_number", sa.String(length=128), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("net_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("gross_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("seller_name", sa.String(length=255), nullable=False),
        sa.Column("seller_orgnr", sa.String(length=32), nullable=True),
        sa.Column("seller_peppol_id", sa.String(length=128), nullable=False),
        sa.Column("buyer_reference", sa.String(length=255), nullable=True),
        sa.Column("order_reference", sa.String(length=255), nullable=True),
        sa.Column("payment_reference", sa.String(length=128), nullable=True),
        sa.Column("seller_bank_account", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("lines_json", sa.Text(), nullable=False),
        sa.Column("tax_subtotals_json", sa.Text(), nullable=False),
        sa.Column("ehf_xml", sa.Text(), nullable=False),
        sa.Column("ehf_sha256", sa.String(length=64), nullable=False),
        sa.Column("has_embedded_pdf", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ny",
                "bokfort",
                "avvist",
                name="peppol_incoming_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("voucher_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_peppol_incoming_document_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voucher_id"],
            ["gl_transaction.id"],
            name="fk_peppol_incoming_document_voucher_id_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_peppol_incoming_document"),
        sa.UniqueConstraint(
            "company_id",
            "transmission_id",
            name="uq_peppol_incoming_company_transmission",
        ),
    )
    op.create_index(
        "ix_peppol_incoming_company_status",
        "peppol_incoming_document",
        ["company_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_peppol_incoming_company_status", table_name="peppol_incoming_document")
    op.drop_table("peppol_incoming_document")
    with op.batch_alter_table("peppol_transmission") as batch_op:
        batch_op.alter_column(
            "remote_document_id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    op.drop_index("uq_peppol_registration_receiving_orgnr", table_name="peppol_registration")
    with op.batch_alter_table("peppol_registration") as batch_op:
        batch_op.drop_column("receiving_enabled_at")
        batch_op.drop_column("receiving_enabled")
        batch_op.drop_column("orgnr")
        batch_op.drop_column("remote_registration_id")
