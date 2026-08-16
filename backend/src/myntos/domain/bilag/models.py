"""The bilag archive: documents attached to a posted voucher.

One table. The file itself lives in :mod:`myntos.storage` (local disk in
development, R2 in production) and this row is the metadata that makes it
findable, verifiable and legally usable.

Three decisions:

**Append-only.** Bokføringsloven § 13 requires primary documentation to stay
available for five years after the financial year ends, so there is no delete
path in the application — not on the model, not on the API. A document attached
to the wrong voucher is superseded by attaching the right one, exactly as a
posted entry is corrected by a reversal rather than an edit.

**The digest is stored.** ``sha256`` is what makes it possible to prove years
later that the PDF handed to an auditor is the one that was uploaded. It also
lets a re-upload of the same file be recognised as such.

**The storage key is opaque and unique.** It is generated, never derived from
the user's filename: a filename arrives from outside and would otherwise decide
where we write on disk. The original name is kept in ``filename`` purely to
show and to download under.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.core.clock import utc_now
from myntos.db.base import Base
from myntos.domain.ledger.models import Transaction

# Altinn caps a single attachment at 25 MB, and a bilag that cannot be sent
# with the melding it documents is not much use.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

PDF_CONTENT_TYPE = "application/pdf"
# Every PDF starts with this. Trusting the browser's Content-Type instead would
# mean the type we store is chosen by the uploader.
PDF_MAGIC = b"%PDF-"


class Attachment(Base):
    """A stored document belonging to one voucher."""

    __tablename__ = "bilag_attachment"
    __table_args__ = (CheckConstraint("byte_size > 0", name="byte_size_is_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Denormalised from the voucher's journal so that every tenancy check and
    # every listing is a single indexed predicate rather than a three-table
    # join. A document is only ever reachable through its own company.
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    transaction_pk: Mapped[int] = mapped_column(ForeignKey("gl_transaction.id", ondelete="CASCADE"))

    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # SET NULL rather than CASCADE: deleting the user who uploaded a bilag must
    # never delete the bilag. GDPR erasure removes the person, not the record.
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    transaction: Mapped[Transaction] = relationship()
