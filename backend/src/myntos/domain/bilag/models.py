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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
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

# --- What may be a bilag -----------------------------------------------------
#
# Bokføringsforskriften § 5-1 says what documentation has to *contain*, never
# what file format it arrives in, and in practice most of a small company's
# purchases are documented by a photograph: a receipt from a shop, a Vipps
# confirmation, a FINN purchase that exists only as a screen. Accepting PDF
# alone meant the register could file the third of a company's bilag that came
# from a supplier with an invoicing system, and told everyone else to go and
# find a printer.
#
# Sniffed from the bytes, never from the upload's Content-Type or its extension,
# for the same reason the PDF check always was: both of those are chosen by
# whoever is uploading, and neither is evidence. The magic numbers below are the
# first bytes of each format's own header.
#
# Every format accepted here has to be one Løynd can read, either as it is (see
# myntos.integrations.loynd.IMAGE_CONTENT_TYPES) or through a copy we make for
# it (READ_AS below). A format accepted here and readable nowhere is a bilag
# that can be filed and never read; the test suite asserts the two line up.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
GIF_MAGIC = (b"GIF87a", b"GIF89a")
# WebP is a RIFF container: "RIFF", four bytes of length, then "WEBP".
RIFF_MAGIC = b"RIFF"
WEBP_TAG = b"WEBP"

# HEIC is what an iPhone camera writes by default, so a receipt photographed on
# one and moved to a laptop by AirDrop arrives as one. It is an ISO base media
# file: a box size, "ftyp", then a four-character major brand, then a list of
# compatible brands. The brand is what says HEVC-coded still image rather than
# any of the other things that container carries (MP4 video, AVIF, a JPEG 2000).
HEIC_CONTENT_TYPE = "image/heic"
FTYP_TAG = b"ftyp"
HEIC_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis"})
# "mif1" only says "an image file of some kind". Plenty of HEIC writers put it
# first and list "heic" after it; AVIF writers do the same with "avif". So mif1
# counts only when a HEIC brand is among the compatible ones.
GENERIC_IMAGE_BRAND = b"mif1"
# The ftyp box of a real file is a few dozen bytes. Reading compatible brands no
# further than this keeps the sniff a header inspection rather than a parse.
_FTYP_MAX_BYTES = 64

ACCEPTED_CONTENT_TYPES = frozenset(
    {PDF_CONTENT_TYPE, "image/png", "image/jpeg", "image/webp", "image/gif", HEIC_CONTENT_TYPE}
)

#: Formats Løynd cannot take as they are, and what we hand it instead. HEIC is
#: patent-encumbered and no browser shows it, so the extractor there does not
#: open it; the archive still keeps the photograph byte for byte, and the reader
#: gets a JPEG made from it (myntos.domain.bilag.derived), the same way a
#: thumbnail is made: a copy, stripped of its location, never the record.
READ_AS = {HEIC_CONTENT_TYPE: "image/jpeg"}

#: What the file dialog should offer, and what the /ki page quotes. Derived from
#: the same set, so an added format cannot be accepted by the backend while the
#: browser still refuses to let anyone pick it.
ACCEPTED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif")


def _is_heic(content: bytes) -> bool:
    """Whether an ISO media header names a HEVC-coded still image."""
    if content[4:8] != FTYP_TAG:
        return False
    major = content[8:12]
    if major in HEIC_BRANDS:
        return True
    if major != GENERIC_IMAGE_BRAND:
        return False
    # The box size says where the brand list ends; capped, because the size is
    # four bytes of the uploader's choosing.
    box_end = min(int.from_bytes(content[0:4], "big"), _FTYP_MAX_BYTES, len(content))
    compatible = content[16:box_end]
    brands = {compatible[i : i + 4] for i in range(0, len(compatible) - 3, 4)}
    return bool(brands & HEIC_BRANDS)


def sniff_content_type(content: bytes) -> str | None:
    """The format these bytes actually are, or ``None`` if it is not one we take.

    Header inspection only — no decoding, no parsing, and certainly no handing
    the bytes to an image library to see whether it copes. Deciding "is this a
    thing we accept" must not itself be the attack surface; the decoding happens
    later, at Løynd behind the guards in ``app/images.py``, or in the sandboxed
    child that makes thumbnails (:mod:`myntos.domain.bilag.derived`). A file that
    passes here and then fails to decode is still filed: it is a bilag nobody
    can preview, which is a smaller problem than one nobody may keep.
    """
    if content.startswith(PDF_MAGIC):
        return PDF_CONTENT_TYPE
    if content.startswith(PNG_MAGIC):
        return "image/png"
    if content.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if content.startswith(GIF_MAGIC):
        return "image/gif"
    if content.startswith(RIFF_MAGIC) and content[8:12] == WEBP_TAG:
        return "image/webp"
    if _is_heic(content):
        return HEIC_CONTENT_TYPE
    return None


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


class VoucherExplanation(Base):
    """Why a voucher was posted, in the words of the person who posted it.

    A payment is documented by somebody else's paper: the receipt, the invoice,
    the bank's statement. An accrual, a reclassification, a change in stock or a
    correction has no such paper, because the company itself decided it, and
    bokføringsforskriften § 5-12 asks that the documentation of such a
    disposition "skal vise årsaken til at disposisjonen gjennomføres og
    bokføres"; for an estimate, § 6-4 asks for the method and the assumptions.
    This is that text, kept beside the voucher as the archive keeps a file.

    Append-only like :class:`Attachment`, and for the same reason: bokføringsloven
    § 10 says documentation is not changed after it is issued. There is no
    update path. A reason that was wrong is answered by reversing the voucher
    with a reason of its own, or by a correction whose replacement carries one.

    One per voucher, written in the same request that posts it. No author or
    time of its own: the voucher's ``created_by_user_id`` and
    ``system_entry_date`` are both, since nothing writes one later. That also
    keeps this table out of the account erasure's list (docs/gdpr.md): the
    person it would name is already named by the voucher.

    Generic on purpose. :mod:`myntos.domain.manuelt_bilag` is the first writer;
    any other voucher a person posts without outside documentation (a
    depreciation, a write-down) may carry one the same way.
    """

    __tablename__ = "voucher_explanation"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Denormalised from the voucher's journal, as on Attachment, so the tenancy
    # check is one predicate on this table.
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    transaction_pk: Mapped[int] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE"), unique=True
    )
    text: Mapped[str] = mapped_column(Text)
