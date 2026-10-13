"""SAF-T files read on the server: ``saft_import`` and ``saft_import_file``.

The previous program's SAF-T files are uploaded once and read, compared and
posted on the server (``myntos.saft.jobs``), so the person can leave the page
while a year of vouchers is read. ``saft_import`` is one upload, its status and
what reading found; ``saft_import_file`` holds each file until the import is
posted or thrown away, and then only its name, size and year.

**What it infers:** nothing. No import ran on the server before this revision:
every earlier SAF-T upload was read in its request and kept by nobody, so there
is no file and no review to bring into the new tables.

**What it refuses to guess:** which of the vouchers in the ``IMPORT`` journal
came from which upload. They were posted through the old routes, which kept no
record of the upload, and inventing a ``saft_import`` row for them would be a
history nobody wrote.

Revision ID: 5b2e8d4f1a63
Revises: 6c1f9e3a7d52
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5b2e8d4f1a63"
down_revision: str | None = "6c1f9e3a7d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "saft_import",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("til", sa.Date(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_via_agent_key_id", sa.Integer(), nullable=True),
        sa.Column("posted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("posted_via_agent_key_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("step", sa.String(length=255), nullable=True),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("review", json, nullable=True),
        sa.Column("result", json, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_saft_import_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_saft_import_created_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_via_agent_key_id"],
            ["agent_key.id"],
            name="fk_saft_import_created_via_agent_key_id_agent_key",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["posted_by_user_id"],
            ["app_user.id"],
            name="fk_saft_import_posted_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["posted_via_agent_key_id"],
            ["agent_key.id"],
            name="fk_saft_import_posted_via_agent_key_id_agent_key",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saft_import"),
    )
    op.create_index("ix_saft_import_company_id", "saft_import", ["company_id"])
    op.create_table(
        "saft_import_file",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["saft_import.id"],
            name="fk_saft_import_file_import_id_saft_import",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saft_import_file"),
    )
    op.create_index("ix_saft_import_file_import_id", "saft_import_file", ["import_id"])


def downgrade() -> None:
    op.drop_index("ix_saft_import_file_import_id", table_name="saft_import_file")
    op.drop_table("saft_import_file")
    op.drop_index("ix_saft_import_company_id", table_name="saft_import")
    op.drop_table("saft_import")
