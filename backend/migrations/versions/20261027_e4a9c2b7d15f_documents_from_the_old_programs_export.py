"""The documents in the old program's export are kept with the books.

Fiken's complete export holds every receipt and invoice behind the vouchers
beside the SAF-T files. Until now only the SAF-T files were taken out and the
rest was never stored, so the person had to keep the export for five years
themselves. Now the ZIP is kept as a working copy beside the SAF-T files taken
out of it (``saft_import_file.kind`` ``export``), and on the person's yes to the
import every file in it is copied into the document store, one row each in
``imported_document`` with the folder path it had in the export.

**What it infers:** that every ``saft_import_file`` row already here is a file
the import reads (``kind`` ``read``): until this revision nothing else was
stored, so there is nothing else for a row to be.

**What it refuses to guess:** which voucher a document belongs to. Fiken does
not publish how the export is laid out, the archive is append-only, and a
document on the wrong voucher there is there for good. The rows keep the path
and are on no voucher; matching them later reads them from here.

Revision ID: e4a9c2b7d15f
Revises: d81f4b6a2c97
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a9c2b7d15f"
down_revision: str | None = "d81f4b6a2c97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "saft_import_file",
        sa.Column("kind", sa.String(16), nullable=False, server_default="read"),
    )
    op.create_table(
        "imported_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=True),
        sa.Column("software", sa.String(length=255), nullable=True),
        sa.Column("export_name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("kept_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kept_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("byte_size >= 0", name="byte_size_is_not_negative"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["saft_import.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["kept_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_imported_document_storage_key"),
    )
    op.create_index(
        "ix_imported_document_company_path", "imported_document", ["company_id", "path"]
    )
    op.create_index(
        "ix_imported_document_company_sha256", "imported_document", ["company_id", "sha256"]
    )


def downgrade() -> None:
    # The rows go; the files they name stay in the document store, which has
    # no delete outside a company's purge. They are the books' documentation,
    # and a downgrade is not a reason to lose them.
    op.drop_index("ix_imported_document_company_sha256", table_name="imported_document")
    op.drop_index("ix_imported_document_company_path", table_name="imported_document")
    op.drop_table("imported_document")
    with op.batch_alter_table("saft_import_file") as batch:
        batch.drop_column("kind")
