"""Human-readable PDF for a received EHF invoice or credit note.

Many Norwegian suppliers embed a PDF inside the EHF XML
(``cac:AdditionalDocumentReference``); when present, Myntos returns and
archives that PDF directly. When the supplier sent UBL XML alone, this module
renders a clean A4 summary in base-14 Helvetica using the same dependency-free
WinAnsi PDF builder as :mod:`myntos.domain.faktura.pdf`, so every booked
incoming EHF has a readable document in the append-only bilag archive.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from myntos.domain.faktura.pdf import (
    MARGIN,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    _build_pdf,
    _Page,
    truncate,
    wrap,
)
from myntos.domain.peppol.models import IncomingDocumentType, PeppolIncomingDocument
from myntos.domain.peppol.ubl_reader import extract_embedded_pdf

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9æøåÆØÅ._-]+")


def _dato(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else "—"


def _amount(value: Decimal | str | None, currency: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            num = Decimal(value)
        except InvalidOperation:
            return value
    else:
        num = value
    formatted = f"{num.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {currency}".strip()


def filename_for(document: PeppolIncomingDocument) -> str:
    """Safe filename for downloading or archiving the PDF of ``document``."""
    kind = (
        "ehf-kreditnota"
        if document.document_type is IncomingDocumentType.KREDITNOTA
        else "ehf-faktura"
    )
    cleaned = _SAFE_FILENAME.sub("-", document.invoice_number.strip()).strip("-")[:60]
    return f"{kind}-{cleaned or document.id}.pdf"


def render_summary(document: PeppolIncomingDocument) -> bytes:
    """Render a readable A4 PDF summary of a received EHF document."""
    right_edge = PAGE_WIDTH - MARGIN
    title = (
        "Innkommende kreditnota (EHF)"
        if document.document_type is IncomingDocumentType.KREDITNOTA
        else "Innkommende faktura (EHF)"
    )

    try:
        lines = json.loads(document.lines_json)
    except ValueError:
        lines = []
    if not isinstance(lines, list):
        lines = []

    try:
        subtotals = json.loads(document.tax_subtotals_json)
    except ValueError:
        subtotals = []
    if not isinstance(subtotals, list):
        subtotals = []

    pages: list[_Page] = []

    def new_page(page_no: int) -> tuple[_Page, float]:
        page = _Page()
        pages.append(page)
        y = PAGE_HEIGHT - MARGIN
        page.text(MARGIN, y, title, size=15, bold=True)
        page.right(right_edge, y + 2, f"Side {page_no}", size=8)
        y -= 18
        page.text(MARGIN, y, truncate(document.seller_name, 11, 340), size=11, bold=True)
        page.right(right_edge, y, f"Nr. {truncate(document.invoice_number, 10, 160)}", size=10)
        y -= 14
        if document.seller_orgnr:
            page.text(MARGIN, y, f"Org.nr. {document.seller_orgnr}", size=9)
        elif document.seller_peppol_id:
            page.text(MARGIN, y, f"Peppol: {document.seller_peppol_id}", size=9)
        page.right(right_edge, y, f"Dato: {_dato(document.issue_date)}", size=9)
        y -= 12
        if document.due_date is not None:
            page.right(right_edge, y, f"Forfall: {_dato(document.due_date)}", size=9)
        if document.seller_bank_account:
            page.text(MARGIN, y, f"Kontonr.: {document.seller_bank_account}", size=9)
        y -= 12
        if document.payment_reference:
            page.text(MARGIN, y, f"KID: {document.payment_reference}", size=9)
        if document.buyer_reference:
            page.right(
                right_edge,
                y,
                f"Ref.: {truncate(document.buyer_reference, 9, 200)}",
                size=9,
            )
        y -= 14
        page.line(MARGIN, y, right_edge, width=0.75, grey=0.5)
        y -= 16
        # Table header
        page.text(MARGIN, y, "Beskrivelse", size=8.5, bold=True)
        page.right(MARGIN + 320, y, "Antall", size=8.5, bold=True)
        page.right(MARGIN + 380, y, "Mva", size=8.5, bold=True)
        page.right(right_edge, y, f"Beløp ({document.currency})", size=8.5, bold=True)
        y -= 6
        page.line(MARGIN, y, right_edge, width=0.5, grey=0.75)
        return page, y - 14

    page, y = new_page(1)
    for item in lines:
        if not isinstance(item, dict):
            continue
        if y < MARGIN + 120:
            page, y = new_page(len(pages) + 1)
        desc = truncate(str(item.get("description") or ""), 9, 255)
        qty = f"{item.get('quantity') or ''} {item.get('unit_code') or ''}".strip()
        vat = f"{item.get('vat_percent')} %" if item.get("vat_percent") is not None else "—"
        net = _amount(item.get("net_amount"))
        page.text(MARGIN, y, desc, size=9)
        page.right(MARGIN + 320, y, truncate(qty, 8.5, 65), size=8.5)
        page.right(MARGIN + 380, y, vat, size=8.5)
        page.right(right_edge, y, net, size=9)
        y -= 14

    if y < MARGIN + 115:
        page, y = new_page(len(pages) + 1)

    y -= 6
    page.line(MARGIN, y, right_edge, width=0.75, grey=0.5)
    y -= 16

    for sub in subtotals:
        if not isinstance(sub, dict):
            continue
        rate = f"{sub.get('percent')} %" if sub.get("percent") is not None else "0 %"
        cat = str(sub.get("category") or "")
        label = f"Mva-grunnlag ({rate}" + (f", {cat})" if cat else ")")
        page.text(MARGIN, y, label, size=8.5)
        page.right(MARGIN + 390, y, _amount(sub.get("taxable_amount")), size=8.5)
        page.right(right_edge, y, _amount(sub.get("tax_amount")), size=8.5)
        y -= 12

    y -= 4
    page.text(MARGIN + 280, y, "Sum eks. mva", size=9)
    page.right(right_edge, y, _amount(document.net_amount, document.currency), size=9)
    y -= 13
    page.text(MARGIN + 280, y, "Merverdiavgift", size=9)
    page.right(right_edge, y, _amount(document.vat_amount, document.currency), size=9)
    y -= 15
    page.text(MARGIN + 280, y, "Å betale", size=10.5, bold=True)
    page.right(
        right_edge, y, _amount(document.gross_amount, document.currency), size=10.5, bold=True
    )
    y -= 18

    if document.note:
        for line in wrap(document.note, 8, right_edge - MARGIN)[:4]:
            if y < MARGIN + 24:
                break
            page.text(MARGIN, y, line, size=8)
            y -= 10

    page.text(
        MARGIN,
        MARGIN,
        f"Mottatt over Peppol (SHA-256: {document.ehf_sha256[:16]}…)",
        size=7.5,
    )
    return _build_pdf(pages)


def pdf_for(document: PeppolIncomingDocument) -> tuple[bytes, str]:
    """Return ``(pdf_bytes, filename)`` — the supplier's embedded PDF or a rendered summary."""
    embedded = extract_embedded_pdf(document.ehf_xml)
    if embedded is not None:
        return embedded
    return render_summary(document), filename_for(document)
