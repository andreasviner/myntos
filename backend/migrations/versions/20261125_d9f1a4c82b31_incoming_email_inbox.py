"""Company email inbox addresses and incoming email invoice attachments.

Creates ``company_inbox_address`` (one unique ``<local_part>@myntos.no`` address
plus per-company sender security policy and allowlist) and
``incoming_mail_document`` (validated PDF/image invoice attachments received by
email with sender metadata and SPF/DKIM/DMARC authentication results).

**What it infers:** assigns a deterministic, collision-free ``local_part`` to
every existing company (oldest company first by ``company.id``) using
:func:`myntos.domain.mottak.address.candidate_local_parts` (e.g. ``Test AS`` ->
``test``, falling back to ``testas``, ``testas2``, ...), enabled for open
companies and disabled for already-closed companies, with default policy
``alle_med_varsel`` and an empty allowlist.

**What it refuses to guess:** any external senders to pre-approve; every
existing company starts with ``allowed_senders_json = '[]'`` and verified
company members are recognised dynamically from ``company_membership``.

Revision ID: d9f1a4c82b31
Revises: c7e3b91a4f20
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.core.clock import utc_now
from myntos.domain.mottak.address import candidate_local_parts

revision: str = "d9f1a4c82b31"
down_revision: str | None = "c7e3b91a4f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_inbox_address",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("local_part", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "sender_policy",
            sa.Enum(
                "alle_med_varsel",
                "kun_godkjente",
                name="mottak_sender_policy",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="alle_med_varsel",
        ),
        sa.Column("allowed_senders_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_company_inbox_address_company_id_company",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_inbox_address"),
        sa.UniqueConstraint("company_id", name="uq_company_inbox_address_company_id"),
        sa.UniqueConstraint("local_part", name="uq_company_inbox_address_local_part"),
    )

    op.create_table(
        "incoming_mail_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("attachment_index", sa.Integer(), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("envelope_from", sa.String(length=320), nullable=False),
        sa.Column("reply_to", sa.String(length=320), nullable=True),
        sa.Column("recipient_address", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("body_excerpt", sa.Text(), nullable=True),
        sa.Column("spf_result", sa.String(length=32), nullable=True),
        sa.Column("dkim_result", sa.String(length=32), nullable=True),
        sa.Column("dmarc_result", sa.String(length=32), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ny",
                "bokfort",
                "avvist",
                name="mottak_mail_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
            server_default="ny",
        ),
        sa.Column("voucher_id", sa.Integer(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_incoming_mail_document_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voucher_id"],
            ["gl_transaction.id"],
            name="fk_incoming_mail_document_voucher_id_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incoming_mail_document"),
        sa.UniqueConstraint("storage_key", name="uq_incoming_mail_document_storage_key"),
        sa.UniqueConstraint(
            "company_id",
            "message_id",
            "attachment_index",
            name="uq_incoming_mail_company_message_att",
        ),
    )
    op.create_index(
        "ix_incoming_mail_company_status",
        "incoming_mail_document",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_incoming_mail_company_received",
        "incoming_mail_document",
        ["company_id", "received_at"],
    )

    # Backfill inbox addresses for existing companies in deterministic id order.
    bind = op.get_bind()
    companies = bind.execute(
        sa.text("SELECT id, name, orgnr, closed_at FROM company ORDER BY id ASC")
    ).fetchall()
    if companies:
        inbox_table = sa.table(
            "company_inbox_address",
            sa.column("company_id", sa.Integer()),
            sa.column("local_part", sa.String(length=64)),
            sa.column("enabled", sa.Boolean()),
            sa.column("sender_policy", sa.String(length=32)),
            sa.column("allowed_senders_json", sa.Text()),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        taken: set[str] = set()
        now = utc_now()
        rows_to_insert: list[dict[str, object]] = []
        for company_id, name, orgnr, closed_at in companies:
            chosen = next(
                candidate_local_parts(str(name), str(orgnr), extra_reserved=frozenset(taken))
            )
            taken.add(chosen)
            rows_to_insert.append(
                {
                    "company_id": int(company_id),
                    "local_part": chosen,
                    "enabled": closed_at is None,
                    "sender_policy": "alle_med_varsel",
                    "allowed_senders_json": "[]",
                    "created_at": now,
                }
            )
        op.bulk_insert(inbox_table, rows_to_insert)


def downgrade() -> None:
    op.drop_index("ix_incoming_mail_company_received", table_name="incoming_mail_document")
    op.drop_index("ix_incoming_mail_company_status", table_name="incoming_mail_document")
    op.drop_table("incoming_mail_document")
    op.drop_table("company_inbox_address")
