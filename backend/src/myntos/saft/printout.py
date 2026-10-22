"""Tripletex's printout of vouchers with their attachments, cut into one PDF per voucher.

Tripletex has no export of the documents behind the vouchers. Asked by the farm
pilot in October 2026, it said it offers no ZIP of the original attachments and
pointed at its help article «Hvordan laster jeg ned regnskapsdataene mine?»:
«Bilag → Bilagsoversikt», choose the period, tick the box in the «Dato» column,
«Skriv ut valgte bilag», and download what it shows as a PDF. That printout is
the only way the documents leave Tripletex in bulk, and it is regular enough to
read:

- every voucher begins with a page Tripletex draws itself, headed «Bilag nummer
  240-2026» twice, with the company («Firma», «Org.nr.»), the voucher's number
  and date, and its posting lines under «Konteringssammendrag»;
- the voucher's attachments follow as pages of their own, until the next such
  page: a supplier's PDF as its pages, a photographed receipt as an image, an
  EHF as Tripletex's rendering of it («Denne visningen er generert fra
  fakturadetaljene»). A voucher with no attachment is its cover page alone.

So the printout is cut at each cover page, and each voucher's pages, cover
included, become one PDF. **The pages are copied, never redrawn**: what lands
in the bilag archive is what Tripletex printed, and an image page stays the
image. The cover stays with its attachments because it is the old program's own
record of the posting and of who booked it («Opprettet», «Sist endret»), which
is what an auditor holding the attachment beside an imported voucher wants to
see next to it.

Pure: bytes in, vouchers and bytes out. Which voucher in Myntos each one belongs
to is :mod:`myntos.saft.documents`.

**Read with pypdf, like every PDF that arrives from outside**
(:mod:`myntos.domain.bilag.text`): pure Python, each page's text layer read,
nothing rendered. Bounded by :data:`MAX_PAGES` and by the upload's size. A page
pypdf cannot read refuses the whole file rather than being passed over: a cover
page read as nothing would put its voucher's attachments on the voucher before.

**Cut the same way twice.** A voucher's PDF is built from its pages alone, with
no file identifier and no date written into it, so cutting the same printout
again gives the same bytes (checked on the farm pilot's September printout,
2026-10-22). That is what lets a second upload of the same month be recognised
by its SHA-256 and skipped, and lets the post cut each file again rather than
hold a year of documents in memory between the review and the yes.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from myntos.domain.bilag.models import PDF_MAGIC
from myntos.saft.errors import InvalidPrintoutError

ZERO = Decimal("0.00")

#: The largest printout read. A month of the farm pilot's is 16 MB for 51
#: vouchers, so a year of it is about 190 MB; Tripletex itself says to print a
#: period at a time when there is much, and that is what a larger one should be.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

#: The most printouts in one upload: two years, a month at a time.
MAX_FILES = 24

#: The most pages read from one printout. 110 pages is the farm's month; this
#: is a busy company's year, and past it the bound is what stops a crafted file
#: from costing a page of text extraction per object it declares.
MAX_PAGES = 10_000

# pypdf says, at warning level, every time it meets a font it reads without
# fontTools, with the font's whole width table in the message. The attachments
# in a printout are invoices from every program there is, and the farm pilot's
# first month put several hundred such lines in the backend's journal in a
# second (2026-10-22), burying the requests around them. Nothing here reads an
# attachment's text, and Tripletex's own pages read fine; what goes wrong for
# real is an exception, which is caught below and said to the person. The
# setting is the process's, so a bilag's text (domain/bilag/text.py) is read as
# quietly; nothing ever acted on those warnings there either.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# Errors pypdf raises on a damaged or hostile file, beyond its own: it is a
# parser walking a stranger's object graph, and these are how that goes wrong.
_PDF_ERRORS = (PyPdfError, ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError)

# The cover page's own words. Matched on the text with Tripletex's no-break
# spaces made plain; the amounts are read from the text as it is (below).
_HEADER = re.compile(r"^\s*Bilag nummer (\d{1,12})-(\d{4})\b")
_SUMMARY = "Konteringssammendrag"
_ORGNR = re.compile(r"Org\.nr\.:\s*(\d{9})\b")
_DATE = re.compile(r"\bDato:\s*(\d{4})-(\d{2})-(\d{2})\b")
_LINE_START = re.compile(r"^\d{4}-\d{2}-\d{2} ")
# Tripletex writes the thousands of an amount with a no-break space and puts a
# plain space between the columns, so «1 1 371,50» (VAT code 1, then the
# amount) is read as the amount it is only when the two are told apart.
_AMOUNT_AT_END = re.compile(r"(-?\d{1,3}(?:\u00a0\d{3})*,\d{2})\s*$")
_SUM_DEBIT = re.compile(r"^Sum debet\s+(-?\d{1,3}(?:[ \u00a0]\d{3})*,\d{2})\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PrintedVoucher:
    """One voucher in the printout: its cover page and the attachments after it."""

    number: str
    year: int
    #: The company the cover names, or None when it names none.
    orgnr: str | None
    #: The voucher's date, «Dato» on the cover.
    on: date | None
    #: Where it starts in the printout, counted from 0, and how many pages it has.
    first_page: int
    pages: int
    #: Pages after the cover: the documents. Zero for a voucher printed alone.
    attachment_pages: int
    #: The cover's debit total, «Sum debet» or the positive lines summed; None
    #: when the cover could not be read that far.
    debit: Decimal | None
    #: The voucher's PDF, when it has attachments: its digest and size.
    sha256: str | None
    size: int | None

    @property
    def label(self) -> str:
        """``240-2026``, as Tripletex writes it."""
        return f"{self.number}-{self.year}"

    @property
    def page_range(self) -> range:
        return range(self.first_page, self.first_page + self.pages)


@dataclass(frozen=True)
class Printout:
    pages: int
    vouchers: tuple[PrintedVoucher, ...]
    #: Pages before the first cover, which belong to no voucher.
    loose_pages: int


@dataclass
class _Voucher:
    number: str
    year: int
    first_page: int
    cover: list[str] = field(default_factory=list)
    attachment_pages: int = 0


def _plain(text: str) -> str:
    return text.replace("\u00a0", " ")


def _open(content: bytes, name: str) -> PdfReader:
    if not content.startswith(PDF_MAGIC):
        raise InvalidPrintoutError(f"«{name}» er ikke en PDF")
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise InvalidPrintoutError(f"«{name}» er passordbeskyttet")
        count = len(reader.pages)
    except _PDF_ERRORS as error:
        raise InvalidPrintoutError(f"«{name}» kunne ikke leses som PDF") from error
    if count > MAX_PAGES:
        raise InvalidPrintoutError(
            f"«{name}» har {count} sider. Myntos leser høyst {MAX_PAGES} sider i hver utskrift: "
            "skriv ut en kortere periode om gangen"
        )
    return reader


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[ \u00a0]", "", text).replace(",", "."))
    except InvalidOperation:
        return None


def _debit(cover: str) -> Decimal | None:
    """What the cover says the voucher's debit side totals.

    «Sum debet» when Tripletex prints it (it does not for a voucher of two
    lines), and otherwise the positive amounts of the posting lines. A line
    runs from its date to the next line's date, since a long text or account
    name wraps; its amount is what it ends with.
    """
    stated = _SUM_DEBIT.search(cover)
    if stated is not None:
        return _amount(stated.group(1))
    if _SUMMARY not in cover:
        return None
    records: list[list[str]] = []
    for line in cover.split(_SUMMARY, 1)[1].splitlines():
        if line.startswith("Sum"):
            break
        if _LINE_START.match(line):
            records.append([line])
        elif records:
            records[-1].append(line)
    total = ZERO
    for record in records:
        found = _AMOUNT_AT_END.search("\n".join(record))
        amount = _amount(found.group(1)) if found is not None else None
        if amount is None:
            return None
        if amount > 0:
            total += amount
    return total if records else None


def _date(cover: str) -> date | None:
    found = _DATE.search(cover)
    if found is None:
        return None
    try:
        return date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    except ValueError:
        return None


def _slice(reader: PdfReader, pages: range) -> bytes:
    writer = PdfWriter()
    for index in pages:
        writer.add_page(reader.pages[index])
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def read(content: bytes, *, name: str) -> Printout:
    """The vouchers in a printout, each with its pages and its PDF's digest.

    Refuses a file that is not a PDF, is closed with a password, cannot be read,
    or has no cover page in it (:class:`InvalidPrintoutError`).
    """
    reader = _open(content, name)
    found: list[_Voucher] = []
    loose = 0
    try:
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            header = _HEADER.match(_plain(text))
            current = found[-1] if found else None
            if header is not None:
                number, year = header.group(1), int(header.group(2))
                same = current is not None and (current.number, current.year) == (number, year)
                # A cover with many lines runs onto the next page under the same
                # header. Only while no attachment has started: after one, the
                # same header is a printout of this voucher somebody attached to
                # it, and stays with it as one of its pages.
                if same and current is not None and current.attachment_pages == 0:
                    current.cover.append(text)
                    continue
                if not same and _SUMMARY in text:
                    found.append(_Voucher(number=number, year=year, first_page=index, cover=[text]))
                    continue
            if current is None:
                loose += 1
            else:
                current.attachment_pages += 1
    except _PDF_ERRORS as error:
        raise InvalidPrintoutError(f"«{name}» kunne ikke leses: en side er skadet") from error
    if not found:
        raise InvalidPrintoutError(
            f"fant ingen bilag i «{name}». Myntos leser utskriften av bilag med vedlegg fra "
            "Tripletex: Bilag → Bilagsoversikt, huk av bilagene og velg «Skriv ut valgte bilag»"
        )

    # Cut from a reader of its own. Reading a page's text leaves pypdf's objects
    # for it changed, and a page cut after that comes out as other bytes than
    # the same page cut by :func:`cut`, which reads no text: four of the farm's
    # 34 documents did, and the post refused them as changed files.
    pristine = _open(content, name)
    vouchers: list[PrintedVoucher] = []
    for voucher in found:
        cover = "\n".join(voucher.cover)
        pages = len(voucher.cover) + voucher.attachment_pages
        digest: str | None = None
        size: int | None = None
        if voucher.attachment_pages > 0:
            try:
                document = _slice(pristine, range(voucher.first_page, voucher.first_page + pages))
            except _PDF_ERRORS as error:
                raise InvalidPrintoutError(
                    f"«{name}»: bilag {voucher.number}-{voucher.year} kunne ikke skilles ut"
                ) from error
            digest = hashlib.sha256(document).hexdigest()
            size = len(document)
        orgnr = _ORGNR.search(_plain(cover))
        vouchers.append(
            PrintedVoucher(
                number=voucher.number,
                year=voucher.year,
                orgnr=orgnr.group(1) if orgnr is not None else None,
                on=_date(_plain(cover)),
                first_page=voucher.first_page,
                pages=pages,
                attachment_pages=voucher.attachment_pages,
                debit=_debit(cover),
                sha256=digest,
                size=size,
            )
        )
    return Printout(pages=len(reader.pages), vouchers=tuple(vouchers), loose_pages=loose)


def cut(
    content: bytes, *, name: str, vouchers: Sequence[PrintedVoucher]
) -> list[tuple[PrintedVoucher, bytes]]:
    """Each of ``vouchers``' PDF, cut from the printout ``read`` found them in.

    Refuses one that does not come out as the bytes ``read`` hashed: the
    printout is not the one the review was made from, and the person said yes
    to that one.
    """
    reader = _open(content, name)
    pieces: list[tuple[PrintedVoucher, bytes]] = []
    for voucher in vouchers:
        try:
            document = _slice(reader, voucher.page_range)
        except _PDF_ERRORS as error:
            raise InvalidPrintoutError(
                f"«{name}»: bilag {voucher.label} kunne ikke skilles ut"
            ) from error
        if hashlib.sha256(document).hexdigest() != voucher.sha256:
            raise InvalidPrintoutError(
                f"«{name}»: bilag {voucher.label} ble ikke det samme som da filen ble lest"
            )
        pieces.append((voucher, document))
    return pieces


__all__ = [
    "MAX_FILES",
    "MAX_PAGES",
    "MAX_UPLOAD_BYTES",
    "PrintedVoucher",
    "Printout",
    "cut",
    "read",
]
