"""A file from the previous program being read on the server, and what reading it found.

The previous program's files used to be uploaded with every request and kept by
nobody, and the page held them in the browser between the read, the accounts
added one request at a time, the second read and the post. A year of vouchers
made that minutes of a tab that could not be closed. Now the files are uploaded
once into a :class:`SaftImport`, read on the server (:mod:`myntos.saft.jobs`),
and the person may leave and come back to the answer.

Mostly SAF-T files, as the name says. Since 2026-10-22 also the old program's
printout of the vouchers with their attachments (``purpose`` ``vedlegg``,
:mod:`myntos.saft.documents`): the same upload, read, review and yes, for a
file that is just as large and just as much a working copy, so the same table
rather than a second one built the same way.

**The files are a working copy, not the archive.** Each is let go as soon as
the import is posted or thrown away, or after
:data:`myntos.saft.jobs.FILE_LIFETIME` with nothing done, the way a waiting
approval's files are (``agent_approval_file``). The name, size and year stay, so
the record still says what was read. Kept in a store of their own
(:mod:`myntos.storage.working`) rather than in the document store, because that
one is append-only on purpose (bokføringsloven § 13), and a file that must be
deleted again does not belong in it.

**What is kept for good** is :class:`ImportedDocument`: every file in the old
program's export (Fiken's complete export: the receipts and invoices behind
the vouchers, the SAF-T files themselves, the spreadsheets), copied into the
document store when the person says yes to the import (:mod:`myntos.saft.archive`).
Those are the documentation of the books before Myntos, which the person would
otherwise have to keep for five years themselves.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.core.clock import utc_now
from myntos.db.base import Base


class ImportPurpose(enum.StrEnum):
    #: Bring the books over: opening balance, accounts, parties, vouchers.
    IMPORT = "import"
    #: Compare the old program's year with Myntos's, during a parallel run.
    SAMMENLIGNING = "sammenligning"
    #: Put the documents from the old program's printout on the vouchers
    #: brought over: the bilag archive, not the books.
    VEDLEGG = "vedlegg"


#: The kinds read from SAF-T files, which the SAF-T routes list.
SAFT_PURPOSES = (ImportPurpose.IMPORT, ImportPurpose.SAMMENLIGNING)


class FileKind(enum.StrEnum):
    #: A file the import reads: a SAF-T file, or a printout.
    READ = "read"
    #: The ZIP a SAF-T file came in, kept whole for the documents beside it
    #: until they are copied into the document store (myntos.saft.archive).
    EXPORT = "export"


class ImportStatus(enum.StrEnum):
    #: The files are being read on the server.
    READING = "reading"
    #: Read: the review is ready, and for an import the person may post it.
    READY = "ready"
    #: Being posted on the server.
    POSTING = "posting"
    #: Posted. The files are gone; the review and the result stay. One whose
    #: documents could not all be kept says so in ``error`` and keeps its
    #: files, so posting it again keeps the rest (myntos.saft.jobs.post).
    POSTED = "posted"
    #: Could not be read (another company's file, a broken one). Says why.
    FAILED = "failed"


class SaftImport(Base):
    __tablename__ = "saft_import"
    __table_args__ = (Index("ix_saft_import_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(16), default=ImportPurpose.IMPORT.value)
    status: Mapped[str] = mapped_column(String(16), default=ImportStatus.READING.value)
    #: The last day to bring over or compare, or None for everything in the files.
    til: Mapped[date | None] = mapped_column(Date)
    # Actor columns are SET NULL, as every one is (docs/gdpr.md): the vouchers
    # an import posts carry their author themselves, and this row is not what
    # keeps a deleted person's name.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
    )
    posted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    #: The key the post was asked through, so the vouchers carry it
    #: (docs/agents-and-api.md rule 6) though no request is running when they
    #: are made.
    posted_via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What the server is doing now, in Norwegian, and how far it has come.
    step: Mapped[str | None] = mapped_column(String(255))
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    #: What reading found: the move's report, or the comparison.
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    #: What posting did.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    #: Why it failed, or why the post was stopped, for the person.
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))

    files: Mapped[list[SaftImportFile]] = relationship(
        back_populates="saft_import",
        cascade="all, delete-orphan",
        order_by="SaftImportFile.position",
        lazy="selectin",
    )


class SaftImportFile(Base):
    __tablename__ = "saft_import_file"
    __table_args__ = (Index("ix_saft_import_file_import_id", "import_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("saft_import.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    #: The year the file turned out to be for, once read.
    year: Mapped[int | None] = mapped_column(Integer)
    #: A file the import reads, or the export kept for its documents (FileKind).
    kind: Mapped[str] = mapped_column(
        String(16), default=FileKind.READ.value, server_default=FileKind.READ.value
    )
    #: Where the file is kept while it is needed (myntos.storage.working), null
    #: once it is gone. For a SAF-T import the stored file is the XML itself,
    #: gzip-compressed, whatever was uploaded: a ZIP has its SAF-T files taken
    #: out at the upload, one row each (myntos.saft.intake). For a printout,
    #: the PDF as it came.
    storage_key: Mapped[str | None] = mapped_column(String(64))
    #: The file in the database, as uploads were kept before 2026-10-27. Never
    #: written now; read for the rows that still have it, which the 14-day rule
    #: empties, after which the column goes in a migration of its own.
    # Deferred: the list of imports, and the import itself, are read far more
    # often than the files, which can be a couple of hundred megabytes each.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)

    saft_import: Mapped[SaftImport] = relationship(back_populates="files")


class ImportedDocument(Base):
    """A file from the old program's export, kept in the document store with the books.

    The receipts and invoices behind the vouchers, as Fiken's complete export
    holds them, and everything else in it. **On no voucher.** Fiken does not
    publish how its export is laid out, and a document put on the wrong voucher
    in an append-only archive is there for good; a document kept under the
    folder it had in the export, findable and downloadable, is what the person
    would otherwise keep themselves. ``path`` is what a later matching to the
    vouchers reads, from here, without anything being uploaded again.

    Append-only like the bilag archive: no route changes or deletes a row, and
    the file goes only with the company's own purge
    (``closure.purge_expired``).
    """

    __tablename__ = "imported_document"
    __table_args__ = (
        Index("ix_imported_document_company_path", "company_id", "path"),
        Index("ix_imported_document_company_sha256", "company_id", "sha256"),
        CheckConstraint("byte_size >= 0", name="byte_size_is_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    #: The import it came with. SET NULL: the import is the record of an
    #: upload, the document is the books' own.
    import_id: Mapped[int | None] = mapped_column(ForeignKey("saft_import.id", ondelete="SET NULL"))
    #: The program the SAF-T files beside it name («Fiken AS»).
    software: Mapped[str | None] = mapped_column(String(255))
    #: The export's name as uploaded (``fiken-komplett-….zip``).
    export_name: Mapped[str] = mapped_column(String(255))
    #: Where it was in the export, folders and all. Text from the export:
    #: shown and searched, never a path on any disk.
    path: Mapped[str] = mapped_column(String(1024))
    #: The name it downloads as, sanitised (bilag_service.sanitise_filename).
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    kept_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # SET NULL like every actor column (docs/gdpr.md): the document is the
    # books', and who said yes to the import is not what keeps it.
    kept_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
