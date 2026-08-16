"""The human-readable salgsdokument, as a PDF.

The EHF XML is what a business customer's system reads and what the law requires
between bokføringspliktige. This is what a person reads: the consumer's copy, the
one attached to an email, and the one that goes into the bilag archive so the
document exists in a form anyone can open in five years without a UBL parser.

**Written by hand, with no PDF library.** The same trade as
:mod:`myntos.storage.sigv4`: what is needed here is a fixed one-page-per-N-lines
layout in Helvetica, which is a few hundred lines of byte assembly, against a
dependency that would have to be trusted with a legally required document and
kept patched for the next decade. The base-14 fonts are guaranteed present in
every conforming reader, so nothing is embedded and the output is a few kilobytes.

Two details that are easy to get wrong:

**Text is encoded WinAnsi (cp1252), not UTF-8.** A base-14 Type1 font with
``/WinAnsiEncoding`` maps single bytes; writing UTF-8 produces mojibake exactly on
æ, ø and å, which is to say on every Norwegian invoice. Characters outside cp1252
are transliterated rather than dropped, so a customer named with a Polish ł gets
``l`` and not a hole.

**Byte offsets in the xref table must be exact.** They are computed while
assembling, never estimated; a table that disagrees with the file by one byte
produces a PDF that some readers repair silently and others refuse.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from myntos.domain.faktura.compliance import describe_markings
from myntos.domain.faktura.models import DocumentType, SalesDocument, SalesDocumentLine
from myntos.domain.faktura.totals import DocumentTotals, totals
from myntos.domain.faktura.vat import VatCategory, percent
from myntos.domain.ledger.posting import ZERO

# A4 in PostScript points.
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 48

REGULAR = "F1"
BOLD = "F2"

# Helvetica advance widths, in 1/1000 em, for the printable ASCII range. Used to
# right-align amounts and to truncate descriptions to their column. Helvetica-Bold
# differs on letters but *not* on digits, space, comma or period — every character
# a right-aligned amount is made of — so one table serves both.
# fmt: off
# Laid out sixteen to a row so the index of a value is countable by eye; the
# formatter would otherwise put each of the ninety-five on its own line.
_ASCII_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
)
# Outside ASCII: the Norwegian letters, the accented ones a customer name may
# carry, and the two dashes and the section sign that this system's own prose
# uses. Every one of them exists in cp1252, so none reaches the transliteration
# path in _encode().
_EXTRA_WIDTHS = {
    "æ": 889, "ø": 611, "å": 556, "Æ": 1000, "Ø": 778, "Å": 667,
    "é": 556, "è": 556, "ü": 556, "ä": 556, "ö": 556, "§": 556,
    "–": 556, "—": 1000,  # noqa: RUF001  en dash, em dash
}
# fmt: on
_FALLBACK_WIDTH = 556


def _char_width(character: str) -> int:
    code = ord(character)
    if 32 <= code <= 126:
        return _ASCII_WIDTHS[code - 32]
    return _EXTRA_WIDTHS.get(character, _FALLBACK_WIDTH)


def text_width(text: str, size: float) -> float:
    """How wide ``text`` renders at ``size`` points."""
    return sum(_char_width(character) for character in text) * size / 1000


def truncate(text: str, size: float, limit: float) -> str:
    """``text`` shortened with an ellipsis until it fits ``limit`` points."""
    if text_width(text, size) <= limit:
        return text
    ellipsis = "…"
    while text and text_width(text + ellipsis, size) > limit:
        text = text[:-1]
    return text.rstrip() + ellipsis


# Letters that carry a stroke or a bar rather than a detachable accent. NFKD
# does not decompose these — ``ł`` has no canonical "l plus combining stroke"
# form — so decomposition alone leaves them to become "?". Polish, Croatian,
# Vietnamese and Turkish names are ordinary on a Norwegian customer list, and a
# question mark in the middle of somebody's name on a legal document is not an
# acceptable answer.
#
# The per-entry noqa markers below are unavoidable: RUF001 flags characters that
# look like ASCII and are not, which is the exact membership criterion for this
# table. The rule is working; the finding is the point.
_TRANSLITERATIONS = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ħ": "h",
        "Ħ": "H",
        "ŧ": "t",
        "Ŧ": "T",
        "ŋ": "n",
        "Ŋ": "N",
        "ı": "i",  # noqa: RUF001
        "ſ": "s",  # noqa: RUF001
        "ß": "ss",
        "œ": "oe",
        "Œ": "OE",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ĸ": "k",
        "€": "EUR",
    }
)


def _encode(text: str) -> bytes:
    """Text as WinAnsi bytes, transliterating anything cp1252 cannot hold.

    Three passes, in decreasing fidelity. Most text encodes directly. What does
    not goes through NFKD, which turns ``ā`` into ``a`` plus a combining macron
    that is then dropped. What survives *that* is looked up in
    ``_TRANSLITERATIONS``, which covers the letters NFKD leaves alone. Only a
    character in none of the three becomes ``?``.

    Norwegian letters never reach any of it: æ, ø and å are in cp1252.
    """
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError:
        folded = unicodedata.normalize("NFKD", text)
        stripped = "".join(c for c in folded if not unicodedata.combining(c))
        return stripped.translate(_TRANSLITERATIONS).encode("cp1252", errors="replace")


def _pdf_string(text: str) -> bytes:
    r"""A PDF literal string: ``(...)`` with ``\``, ``(`` and ``)`` escaped."""
    raw = _encode(text)
    for old, new in ((b"\\", b"\\\\"), (b"(", b"\\("), (b")", b"\\)")):
        raw = raw.replace(old, new)
    return b"(" + raw + b")"


@dataclass
class _Page:
    """One page's content stream, built as a list of operators."""

    operators: list[bytes] = field(default_factory=list)

    def text(self, x: float, y: float, value: str, *, size: float = 9, bold: bool = False) -> None:
        if not value:
            return
        self.operators.append(
            b"BT /%s %s Tf 1 0 0 1 %s %s Tm %s Tj ET"
            % (
                (BOLD if bold else REGULAR).encode("ascii"),
                _number(size),
                _number(x),
                _number(y),
                _pdf_string(value),
            )
        )

    def right(self, x: float, y: float, value: str, *, size: float = 9, bold: bool = False) -> None:
        """Draw ``value`` ending at ``x`` — the only way a column of amounts lines up."""
        self.text(x - text_width(value, size), y, value, size=size, bold=bold)

    def line(
        self, x1: float, y: float, x2: float, *, width: float = 0.5, grey: float = 0.75
    ) -> None:
        self.operators.append(
            b"q %s G %s w %s %s m %s %s l S Q"
            % (_number(grey), _number(width), _number(x1), _number(y), _number(x2), _number(y))
        )

    def render(self) -> bytes:
        return b"\n".join(self.operators)


def _number(value: float) -> bytes:
    """A PDF numeric literal: two decimals at most, never exponent notation."""
    text = f"{value:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return (text or "0").encode("ascii")


def _build_pdf(pages: list[_Page]) -> bytes:
    """Assemble pages into a PDF file, with a correct cross-reference table.

    Object numbering is fixed: 1 catalog, 2 page tree, 3 and 4 the two fonts,
    then a (page, contents) pair per page. Fixing it keeps the xref construction
    a single pass instead of a fixup pass over placeholders.
    """
    objects: dict[int, bytes] = {}
    page_ids = [5 + index * 2 for index in range(len(pages))]

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages))
    objects[3] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects[4] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    for index, page in enumerate(pages):
        page_id = page_ids[index]
        contents_id = page_id + 1
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /%s 3 0 R /%s 4 0 R >> >> /Contents %d 0 R >>"
            % (
                PAGE_WIDTH,
                PAGE_HEIGHT,
                REGULAR.encode("ascii"),
                BOLD.encode("ascii"),
                contents_id,
            )
        )
        stream = page.render()
        objects[contents_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)

    out = bytearray(b"%PDF-1.4\n")
    # A binary comment marks the file as containing binary data, so a transport
    # that guesses at text does not mangle it.
    out += b"%\xe2\xe3\xcf\xd3\n"
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])

    xref_offset = len(out)
    count = len(objects) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for number in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (count, xref_offset)
    return bytes(out)


# --- Layout ---------------------------------------------------------------------


def _kroner(amount: Decimal) -> str:
    """``12345.6`` → ``12 345,60`` — Norwegian, and without a currency symbol.

    The currency is stated once in the column heading rather than on every row;
    repeating "kr" down a column is noise that also makes the digits harder to
    scan.
    """
    quantized = amount.quantize(Decimal("0.01"))
    # An ASCII hyphen, not the typographic U+2212 MINUS SIGN: cp1252 has no
    # code point for the latter, so it would be transliterated on its way into
    # the content stream and a negative amount would print as a positive one.
    sign = "-" if quantized < 0 else ""
    whole, _, fraction = f"{abs(quantized):.2f}".partition(".")
    grouped = ""
    for index, digit in enumerate(reversed(whole)):
        if index and index % 3 == 0:
            grouped = " " + grouped
        grouped = digit + grouped
    return f"{sign}{grouped},{fraction}"


def _quantity(value: Decimal) -> str:
    normalised = value.normalize()
    text = format(normalised, "f")
    return text.replace(".", ",")


COLUMNS = {
    "description": MARGIN,
    "quantity": 330.0,
    "unit": 370.0,
    "price": 452.0,
    "vat": 490.0,
    "amount": PAGE_WIDTH - MARGIN,
}


def _header(page: _Page, document: SalesDocument, *, page_no: int, page_count: int) -> float:
    """The seller block, the title block and the buyer block. Returns the next y."""
    top = PAGE_HEIGHT - MARGIN
    right = PAGE_WIDTH - MARGIN

    title = "KREDITNOTA" if document.is_credit_note else "FAKTURA"
    page.text(MARGIN, top - 4, document.seller_name, size=15, bold=True)
    page.right(right, top - 4, title, size=15, bold=True)

    # Annotated: the page geometry constants are ints, but text_width() and the
    # half-point line spacings below make every later assignment a float.
    y: float = top - 22
    for value in (
        document.seller_address_line,
        " ".join(filter(None, (document.seller_postal_code, document.seller_city))),
    ):
        if value:
            page.text(MARGIN, y, value, size=8.5)
            y -= 11

    # § 5-1-2: the orgnr, with "MVA" after it when the seller is registered.
    suffix = " MVA" if document.seller_vat_registered else ""
    page.text(MARGIN, y, f"Org.nr. {document.seller_orgnr}{suffix}", size=8.5)
    y -= 11
    for marking in describe_markings(document):
        if marking != "Kreditnota":
            page.text(MARGIN, y, marking, size=8.5, bold=True)
            y -= 11

    # The title block, right-aligned against the seller block.
    meta = [("Nr.", document.document_number or "utkast")]
    if document.issue_date is not None:
        meta.append(("Fakturadato", document.issue_date.isoformat()))
    if document.delivery_date is not None:
        meta.append(("Leveringsdato", document.delivery_date.isoformat()))
    if document.due_date is not None and not document.is_credit_note:
        meta.append(("Forfall", document.due_date.isoformat()))
    if page_count > 1:
        meta.append(("Side", f"{page_no} av {page_count}"))

    meta_y: float = top - 24
    for label, value in meta:
        page.right(right - 90, meta_y, label, size=8.5)
        page.right(right, meta_y, value, size=8.5, bold=label == "Nr.")
        meta_y -= 12

    y = min(y, meta_y) - 14
    page.text(MARGIN, y, "Faktureres til", size=7.5)
    y -= 13
    page.text(MARGIN, y, document.buyer_name, size=10, bold=True)
    y -= 12
    for value in (
        document.buyer_address_line,
        " ".join(filter(None, (document.buyer_postal_code, document.buyer_city))),
        f"Org.nr. {document.buyer_orgnr}" if document.buyer_orgnr else None,
    ):
        if value:
            page.text(MARGIN, y, value, size=8.5)
            y -= 11

    details = []
    if document.buyer_reference:
        details.append(("Deres referanse", document.buyer_reference))
    if document.order_reference:
        details.append(("Ordrenummer", document.order_reference))
    if document.delivery_place:
        details.append(("Leveringssted", document.delivery_place))
    detail_y = y + 11 + 12 * len(details)
    for label, value in details:
        page.text(right - 200, detail_y, label, size=7.5)
        page.right(right, detail_y, truncate(value, 8.5, 130), size=8.5)
        detail_y -= 12

    return min(y, detail_y) - 16


def _table_head(page: _Page, y: float) -> float:
    page.text(COLUMNS["description"], y, "Beskrivelse", size=7.5, bold=True)
    page.right(COLUMNS["quantity"], y, "Antall", size=7.5, bold=True)
    page.text(COLUMNS["unit"], y, "Enhet", size=7.5, bold=True)
    page.right(COLUMNS["price"], y, "Pris", size=7.5, bold=True)
    page.right(COLUMNS["vat"], y, "Mva", size=7.5, bold=True)
    page.right(COLUMNS["amount"], y, "Beløp NOK", size=7.5, bold=True)
    page.line(MARGIN, y - 5, PAGE_WIDTH - MARGIN)
    return y - 17


def _foot(page: _Page, document: SalesDocument, computed: DocumentTotals, y: float) -> None:
    """The § 5-1-5 breakdown, the totals, and how to pay."""
    right = PAGE_WIDTH - MARGIN
    page.line(MARGIN, y + 10, right)
    y -= 2

    # § 5-1-5: avgiftspliktig, avgiftsfritt and unntatt omsetning each shown and
    # summed for itself, and each rate separately.
    page.right(COLUMNS["price"], y, "Grunnlag", size=7.5)
    page.right(right, y, "Merverdiavgift", size=7.5)
    y -= 13
    for subtotal in computed.subtotals:
        page.text(MARGIN, y, _subtotal_label(subtotal.category, subtotal.percent), size=8.5)
        page.right(COLUMNS["price"], y, _kroner(subtotal.taxable_amount), size=8.5)
        page.right(right, y, _kroner(subtotal.tax_amount), size=8.5)
        y -= 12

    y -= 4
    page.line(MARGIN + 300, y + 8, right)
    page.text(MARGIN + 300, y - 4, "Sum eks. mva", size=9)
    page.right(right, y - 4, _kroner(computed.tax_exclusive), size=9)
    page.text(MARGIN + 300, y - 18, "Merverdiavgift", size=9)
    page.right(right, y - 18, _kroner(computed.tax_total), size=9)
    page.line(MARGIN + 300, y - 24, right)
    label = "Å betale" if not document.is_credit_note else "Krediteres"
    page.text(MARGIN + 300, y - 38, label, size=11, bold=True)
    page.right(right, y - 38, f"{_kroner(computed.payable)} kr", size=11, bold=True)

    y -= 62
    for reason in _exemption_reasons(computed):
        page.text(MARGIN, y, reason, size=7.5)
        y -= 10

    if document.note:
        y -= 6
        page.text(MARGIN, y, truncate(document.note, 8.5, PAGE_WIDTH - 2 * MARGIN), size=8.5)
        y -= 12

    if document.is_credit_note:
        return
    payment = []
    if document.seller_bank_account:
        payment.append(f"Kontonummer {document.seller_bank_account}")
    if document.payment_reference:
        payment.append(f"KID {document.payment_reference}")
    if document.due_date is not None:
        payment.append(f"Forfall {document.due_date.isoformat()}")
    if payment:
        y -= 6
        page.text(MARGIN, y, "   ".join(payment), size=9, bold=True)


def _subtotal_label(category: VatCategory, rate_percent: Decimal) -> str:
    if category is VatCategory.STANDARD:
        return f"Avgiftspliktig omsetning, {rate_percent} %"
    return {
        VatCategory.ZERO_RATED: "Avgiftsfri omsetning (0 %)",
        VatCategory.EXEMPT: "Omsetning unntatt merverdiavgift",
        VatCategory.EXPORT: "Utførsel, avgiftsfritt",
        VatCategory.REVERSE_CHARGE: "Omvendt avgiftsplikt",
        VatCategory.OUT_OF_SCOPE: "Utenfor merverdiavgiftsloven",
        VatCategory.EEA_SUPPLY: "Levering innen EØS",
    }[category]


def _exemption_reasons(computed: DocumentTotals) -> list[str]:
    """The stated reason behind every zero-rated group, deduplicated in order."""
    seen: list[str] = []
    for subtotal in computed.subtotals:
        if subtotal.reason and subtotal.reason not in seen:
            seen.append(subtotal.reason)
    return seen


# How much vertical room the foot needs. Lines stop above it and continue on a
# new page rather than being drawn over the totals.
def _foot_height(computed: DocumentTotals) -> float:
    return 150 + 12 * len(computed.subtotals) + 10 * len(_exemption_reasons(computed))


def _row_height(line: SalesDocumentLine) -> float:
    """How much vertical room one line needs, discount note included."""
    return 14 + (10 if line.discount_rate > ZERO else 0)


def _paginate(document: SalesDocument, computed: DocumentTotals) -> list[list[SalesDocumentLine]]:
    """Split the lines across pages.

    Done as its own pass because the page *count* has to be known before the
    first header is drawn — a header that said "Side 1 av 1" and was later
    corrected would mean either rendering twice or editing operators after the
    fact, and editing them means matching on their bytes.

    The last page has to hold the foot as well, so the final chunk is measured
    against a higher floor. A page break can therefore happen even when the
    remaining lines would have fitted the page but not the totals under them.
    """
    probe = _Page()
    start = _table_head(probe, _header(probe, document, page_no=1, page_count=2))
    line_floor = MARGIN + 24
    foot_floor = MARGIN + _foot_height(computed)

    pages: list[list[SalesDocumentLine]] = [[]]
    y = start
    remaining = list(document.lines)
    while remaining:
        line = remaining[0]
        height = _row_height(line)
        # The floor is the taller one only when this is the last line left,
        # since only then does the foot land on this page.
        floor = foot_floor if len(remaining) == 1 else line_floor
        if y - height < floor and pages[-1]:
            pages.append([])
            y = start
            continue
        pages[-1].append(line)
        remaining.pop(0)
        y -= height

    # An empty document still gets one page, and a full last page still needs
    # somewhere to put the totals.
    if y - _foot_height(computed) < MARGIN and pages[-1]:
        pages.append([])
    return pages


def render(document: SalesDocument) -> bytes:
    """``document`` as PDF bytes.

    Works on a draft as well as an issued document — a draft simply prints
    "utkast" where the number goes, which is what makes a preview possible before
    anything is numbered or posted.
    """
    computed = totals(document)
    chunks = _paginate(document, computed)
    pages: list[_Page] = []

    for index, chunk in enumerate(chunks):
        page = _Page()
        pages.append(page)
        y = _table_head(page, _header(page, document, page_no=index + 1, page_count=len(chunks)))
        for line in chunk:
            description = truncate(line.description, 9, COLUMNS["quantity"] - MARGIN - 34)
            page.text(COLUMNS["description"], y, description, size=9)
            page.right(COLUMNS["quantity"], y, _quantity(line.quantity), size=9)
            page.text(COLUMNS["unit"], y, line.unit_code, size=9)
            page.right(COLUMNS["price"], y, _kroner(line.unit_price), size=9)
            page.right(COLUMNS["vat"], y, _vat_label(document, line.tax_rate), size=9)
            page.right(COLUMNS["amount"], y, _kroner(line.net_amount), size=9)
            y -= 14
            if line.discount_rate > ZERO:
                page.text(
                    COLUMNS["description"] + 8,
                    y + 3,
                    f"Rabatt {percent(line.discount_rate)} %",
                    size=7.5,
                )
                y -= 10
        if index == len(chunks) - 1:
            _foot(page, document, computed, min(y, MARGIN + _foot_height(computed)))

    return _build_pdf(pages)


def _vat_label(document: SalesDocument, rate: Decimal) -> str:
    """The rate as it appears in the line's Mva column.

    "OA" for omvendt avgiftsplikt: the column has room for two characters, and
    the full marking is printed under the seller block where § 5-1-1 nr. 7 wants
    it anyway.
    """
    if document.reverse_charge:
        return "OA"
    return f"{percent(rate)} %"


def filename_for(document: SalesDocument) -> str:
    """What the archived and downloaded file is called."""
    kind = "kreditnota" if document.document_type is DocumentType.KREDITNOTA else "faktura"
    return f"{kind}-{document.document_number or 'utkast'}.pdf"
