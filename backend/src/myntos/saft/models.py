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

**The files are a working copy, not the archive.** ``content`` is emptied as
soon as the import is posted or thrown away, or after
:data:`myntos.saft.jobs.FILE_LIFETIME` with nothing done, the way a waiting
approval's files are (``agent_approval_file``). The name, size and year stay, so
the record still says what was read. Kept in the database rather than in the
document store because the store is append-only on purpose (bokføringsloven
§ 13), and a file that must be deleted again does not belong in it. The person
keeps the SAF-T files themselves, as the documentation of the books before
Myntos.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class ImportStatus(enum.StrEnum):
    #: The files are being read on the server.
    READING = "reading"
    #: Read: the review is ready, and for an import the person may post it.
    READY = "ready"
    #: Being posted on the server.
    POSTING = "posting"
    #: Posted. The files are gone; the review and the result stay.
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
    # Deferred: the list of imports, and the import itself, are read far more
    # often than the files, which can be a couple of hundred megabytes each.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)

    saft_import: Mapped[SaftImport] = relationship(back_populates="files")
