"""The skattemelding filed from the books: ``skattemelding_filing`` and its documents.

For :mod:`myntos.skattemelding` and the routes under
``/api/v1/companies/{company_id}/skattemelding``. A row is one check of an
income year's skattemelding and næringsspesifikasjon with Skatteetaten,
holding the exact envelope that was checked and Skatteetaten's whole answer,
and, once the person sends it, the Altinn instance and who sent it. The shape
is ``mva_filing``'s (:mod:`myntos.skattemelding.models` says why), with three
things of its own: the taxpayer number Skatteetaten gave
(``partsnummer``, ten digits, so a BIGINT), the person's answer that went
into the melding (``answers``), and whether Enhetsregisteret named a revisor
(``has_auditor``), which decides the Altinn instance's revisor step.

``skattemelding_filing_document`` holds what Skatteetaten leaves on the
instance after processing it, as ``mva_filing_document`` does.

Both actor columns are SET NULL and kept as a tombstone by erasure, like
``mva_filing``'s (docs/gdpr.md).

**What it infers:** nothing. Both tables start empty.

**What it refuses to guess:** that any year was filed. Until now a company
filed its skattemelding somewhere else, and a row here says Myntos sent it.

Revision ID: b7e3d91c4a06
Revises: a84dfde214d1
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e3d91c4a06"
down_revision: str | None = "a84dfde214d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "skattemelding_filing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("melding_id", sa.String(length=64), nullable=False),
        sa.Column("partsnummer", sa.BigInteger(), nullable=False),
        sa.Column("request_xml", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("answers", json_type, nullable=False),
        sa.Column("has_auditor", sa.Boolean(), nullable=False),
        sa.Column("aarsresultat", sa.Numeric(38, 2), nullable=False),
        sa.Column("skattemessig_resultat", sa.Numeric(38, 2), nullable=False),
        sa.Column("beregnet_skatt", sa.Numeric(38, 2), nullable=True),
        sa.Column("findings", json_type, nullable=False),
        sa.Column("validation_status", sa.String(length=64), nullable=False),
        sa.Column("validation_xml", sa.Text(), nullable=False),
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("altinn_instance_id", sa.String(length=80), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by_name", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=255), nullable=True),
        sa.Column("outcome_text", sa.Text(), nullable=True),
        sa.Column("feedback_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_skattemelding_filing_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_skattemelding_filing_created_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["app_user.id"],
            name="fk_skattemelding_filing_submitted_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skattemelding_filing"),
    )
    op.create_index(
        "ix_skattemelding_filing_company_id_year",
        "skattemelding_filing",
        ["company_id", "year"],
    )
    op.create_table(
        "skattemelding_filing_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["skattemelding_filing.id"],
            name="fk_skattemelding_filing_document_filing_id_skattemelding_filing",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skattemelding_filing_document"),
        sa.UniqueConstraint(
            "filing_id", "data_type", name="uq_skattemelding_filing_document_filing_id"
        ),
        sa.UniqueConstraint("storage_key", name="uq_skattemelding_filing_document_storage_key"),
    )


def downgrade() -> None:
    op.drop_table("skattemelding_filing_document")
    op.drop_index("ix_skattemelding_filing_company_id_year", table_name="skattemelding_filing")
    op.drop_table("skattemelding_filing")
