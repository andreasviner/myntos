"""The hand-written PDF.

Two classes of thing are worth testing on a PDF nobody parses semantically.

**Structural validity.** The cross-reference table is a list of byte offsets, and
a wrong one produces a file that some readers repair silently and others refuse —
a failure mode that would never show up in a browser preview during development
and would show up in an auditor's reader in 2031. So the tests parse the xref
back out and check every offset points at the object it claims.

**Text encoding.** Base-14 fonts with ``/WinAnsiEncoding`` map single bytes;
writing UTF-8 would produce mojibake on exactly æ, ø and å. The tests read the
content stream back and assert on cp1252 bytes.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

from myntos.domain.faktura import pdf
from myntos.domain.faktura.models import DocumentType, InvoiceStatus, SalesDocument
from myntos.domain.faktura.vat import REVERSE_CHARGE_MARKING
from tests.test_faktura_domain import make_document, make_line

XREF_ENTRY = re.compile(rb"^(\d{10}) (\d{5}) ([nf]) $", re.MULTILINE)


def issued(**overrides: Any) -> SalesDocument:
    document = make_document(**overrides)
    document.status = InvoiceStatus.UTSTEDT
    document.document_number = "1"
    document.issue_date = date(2026, 8, 16)
    return document


def content_streams(raw: bytes) -> bytes:
    """Every content stream concatenated, for text assertions."""
    return b"".join(re.findall(rb"stream\n(.*?)\nendstream", raw, re.DOTALL))


def test_the_output_is_a_pdf() -> None:
    raw = pdf.render(issued())
    assert raw.startswith(b"%PDF-1.4")
    assert raw.rstrip().endswith(b"%%EOF")


def test_every_xref_offset_points_at_the_object_it_claims() -> None:
    """A table that disagrees with the file by one byte is a broken PDF."""
    raw = pdf.render(issued())

    startxref = int(raw.rsplit(b"startxref", maxsplit=1)[1].split(b"%%EOF")[0].strip())
    assert raw[startxref : startxref + 4] == b"xref"

    entries = XREF_ENTRY.findall(raw[startxref:])
    # Entry 0 is the mandatory free head; the rest are objects 1..n in order.
    assert entries[0][2] == b"f"
    for number, (offset, _generation, kind) in enumerate(entries[1:], start=1):
        assert kind == b"n"
        assert raw[int(offset) :].startswith(b"%d 0 obj" % number), f"object {number}"

    # /Size counts the free entry too.
    size = int(re.search(rb"/Size (\d+)", raw).group(1))  # type: ignore[union-attr]
    assert size == len(entries)


def test_stream_lengths_match_the_declared_length() -> None:
    raw = pdf.render(issued())
    for declared, stream in re.findall(
        rb"<< /Length (\d+) >>\nstream\n(.*?)\nendstream", raw, re.DOTALL
    ):
        assert len(stream) == int(declared)


def test_norwegian_letters_are_written_as_winansi_bytes() -> None:
    raw = pdf.render(issued(buyer_name="Grønn Ørn Ærlig AS"))
    streams = content_streams(raw)

    assert "Grønn Ørn Ærlig AS".encode("cp1252") in streams
    # And emphatically not UTF-8, which the font would render as mojibake.
    assert "Grønn".encode() not in streams


def test_characters_outside_cp1252_are_transliterated_not_dropped() -> None:
    """A question mark in the middle of a customer's name is not an acceptable answer.

    ``ł`` is the awkward case: NFKD does not decompose it, so the stroke-letter
    table is what saves it. ``ā`` exercises the decomposition path instead.
    """
    raw = pdf.render(issued(buyer_name="Wesoły Sklep"))
    assert b"Wesoly Sklep" in content_streams(raw)

    raw = pdf.render(issued(buyer_name="Jānis Bērziņš SIA"))
    assert b"Janis Berzins SIA" in content_streams(raw)


def test_parentheses_in_a_name_cannot_break_out_of_the_string() -> None:
    """An unescaped ``)`` would end the string and corrupt the rest of the page."""
    raw = pdf.render(issued(buyer_name="Hansen (Norge) AS"))
    assert rb"Hansen \(Norge\) AS" in content_streams(raw)


def test_the_seller_orgnr_carries_the_mva_suffix_when_registered() -> None:
    registered = content_streams(pdf.render(issued(vat_registered=True)))
    assert b"Org.nr. 910000004 MVA" in registered

    unregistered = content_streams(
        pdf.render(issued(vat_registered=False, lines=[make_line(tax_code="0")]))
    )
    assert b"Org.nr. 910000004" in unregistered
    assert b"MVA" not in unregistered.split(b"Org.nr. 910000004")[1][:20]


def test_the_foretaksregisteret_marking_is_printed() -> None:
    streams = content_streams(pdf.render(issued(seller_in_foretaksregisteret=True)))
    assert b"Foretaksregisteret" in streams


def test_the_reverse_charge_marking_is_printed_verbatim() -> None:
    """§ 5-1-1 nr. 7 prescribes the wording, so it is not paraphrased."""
    streams = content_streams(pdf.render(issued(reverse_charge=True)))
    assert REVERSE_CHARGE_MARKING.encode("cp1252") in streams


def test_amounts_are_formatted_norwegian() -> None:
    streams = content_streams(pdf.render(issued(lines=[make_line(unit_price="1234567.80")])))
    # Thin-spaced thousands and a decimal comma.
    assert b"1 234 567,80" in streams
    assert b"1 543 209,75" in streams  # gross, at 25 %


def test_negative_amounts_use_a_hyphen_cp1252_can_encode() -> None:
    assert pdf._kroner(Decimal("-42.50")) == "-42,50"
    assert "-42,50".encode("cp1252") == b"-42,50"


def test_the_vat_breakdown_is_printed_group_by_group() -> None:
    """§ 5-1-5: each rate and each category shown and summed for itself."""
    document = issued(
        lines=[
            make_line(1, unit_price="1000.00", tax_code="3"),
            make_line(2, unit_price="200.00", tax_code="32"),
            make_line(3, unit_price="100.00", tax_code="52"),
        ]
    )
    streams = content_streams(pdf.render(document))
    assert "Avgiftspliktig omsetning, 25 %".encode("cp1252") in streams
    assert "Avgiftspliktig omsetning, 12 %".encode("cp1252") in streams
    assert "Utførsel, avgiftsfritt".encode("cp1252") in streams


def test_a_draft_prints_utkast_where_the_number_goes() -> None:
    """Which is what makes a preview possible before anything is numbered."""
    draft = make_document()
    streams = content_streams(pdf.render(draft))
    assert b"utkast" in streams
    assert b"FAKTURA" in streams


def test_a_credit_note_says_so() -> None:
    document = issued()
    document.document_type = DocumentType.KREDITNOTA
    streams = content_streams(pdf.render(document))
    assert b"KREDITNOTA" in streams
    assert "Krediteres".encode("cp1252") in streams


def test_many_lines_spill_onto_further_pages() -> None:
    document = issued(
        lines=[make_line(index, description=f"Linje {index}") for index in range(1, 61)]
    )
    raw = pdf.render(document)

    count = int(re.search(rb"/Count (\d+)", raw).group(1))  # type: ignore[union-attr]
    assert count > 1
    assert len(re.findall(rb"/Type /Page[^s]", raw)) == count
    streams = content_streams(raw)
    # Every line made it, and the page numbering says how many there are. The
    # label and the value are separate Tj operations — the header draws every
    # meta row as a right-aligned pair — so they are asserted separately.
    assert b"Linje 60" in streams
    assert b"(Side)" in streams
    for page_no in range(1, count + 1):
        assert b"(%d av %d)" % (page_no, count) in streams


def test_a_single_page_invoice_says_nothing_about_pages() -> None:
    assert b"Side" not in content_streams(pdf.render(issued()))


def test_long_descriptions_are_truncated_rather_than_overrunning_the_column() -> None:
    document = issued(lines=[make_line(description="A" * 300)])
    streams = content_streams(pdf.render(document))
    assert b"A" * 300 not in streams
    assert "…".encode("cp1252") in streams


def test_rendering_is_deterministic() -> None:
    """The archived copy and a later download have to be the same bytes."""
    document = issued()
    assert pdf.render(document) == pdf.render(document)


def test_filename_names_the_document_type_and_number() -> None:
    assert pdf.filename_for(issued()) == "faktura-1.pdf"
    credit = issued()
    credit.document_type = DocumentType.KREDITNOTA
    assert pdf.filename_for(credit) == "kreditnota-1.pdf"
