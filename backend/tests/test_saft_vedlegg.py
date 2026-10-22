"""The documents behind the vouchers, from Tripletex's printout into the bilag archive.

The books are the two years ``test_saft_innlesing`` brings over from synthetic
Tripletex files. The printout is written by hand in the shape of the farm
pilot's real one (``dump/`` and ``regnaskaps_tests/`` stay out of the
repository): for each voucher a page headed «Bilag nummer 17-2025» twice, with
the company's organisasjonsnummer and the posting lines under
«Konteringssammendrag», the amounts' thousands written with a no-break space,
and the attachments after it, a scanned one as a page with no text at all.

Background work runs inside the request under the test client, so an upload is
read, and archived, by the time the answer is in hand.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from myntos.saft import printout
from myntos.saft.errors import InvalidPrintoutError
from tests.conftest import agent_header, join_company, write
from tests.test_arsoppgjor_api import LAST_YEAR, _books
from tests.test_cashflow_api import INVOICE_LINES, OTHER_ORGNR, text_pdf
from tests.test_saft_import import ORGNR
from tests.test_saft_innlesing import EARLIER, _both_years
from tests.test_saft_innlesing import _get as _get_import
from tests.test_saft_innlesing import _post as _post_import

Y = LAST_YEAR


# --- A printout, written by hand ---------------------------------------------------------


def pages_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with one page per entry, each holding its lines as text.

    An empty entry is a page with nothing in its text layer, which is what a
    scanned receipt is to a reader that does not do OCR. Written out by hand for
    the reason ``test_cashflow_api.text_pdf`` gives; WinAnsi, so «ø» and the
    no-break space come back as themselves.
    """
    objects: list[bytes] = [
        b"",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    kids: list[int] = []
    for lines in pages:
        drawn = ["BT", "/F1 9 Tf", "12 TL", "40 800 Td"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            drawn.append(f"({escaped}) Tj T*")
        drawn.append("ET")
        stream = "\n".join(drawn).encode("cp1252") if lines else b""
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        content_number = len(objects)
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(content_number).encode()
            + b" 0 R >>"
        )
        kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{kid} 0 R".encode() for kid in kids)
        + b"] /Count "
        + str(len(kids)).encode()
        + b" >>"
    )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def nok(amount: str) -> str:
    """``-1 000,00`` with Tripletex's no-break space between the thousands."""
    value = Decimal(amount)
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


def cover(
    number: str,
    year: int,
    lines: list[tuple[str, str, str, str]],
    *,
    orgnr: str = ORGNR,
    sum_debit: str | None = None,
) -> list[str]:
    """Tripletex's own page for a voucher: lines of (date, text, account, amount)."""
    printed = [
        f"Bilag nummer {number}-{year} Bilag nummer {number}-{year}",
        "Firma: Overgang AS",
        f"Org.nr.: {orgnr}",
        "Bilag",
        f"Nummer: {number}-{year}",
        f"Dato: {lines[0][0] if lines else f'{year}-01-01'}",
        "Opprettet: 2026-10-01 12:36:25 System",
        "Konteringssammendrag",
        "Dato Beskrivelse Konto Avdeling Mva Beløp",
    ]
    for on, text, account, amount in lines:
        printed.append(f"{on} {text} {account} {nok(amount)}")
    if sum_debit is not None:
        printed += [f"Sum debet {sum_debit}", f"Sum kredit {sum_debit}"]
    printed.append("Sum 0,00")
    return printed


SCAN: list[str] = []

#: Five vouchers, as Tripletex prints them: one from the year before with its
#: invoice; one whose payment came on a later day (two vouchers in Myntos), with
#: a page of text and a scan; one printed alone; one whose cover says another
#: debit total than the books; and one Myntos never got.
PRINTOUT_PAGES = [
    cover(
        "17",
        EARLIER,
        [
            (f"{EARLIER}-06-15", "Melk solgt", "1920 Bank", "1250.00"),
            (f"{EARLIER}-06-15", "Melk solgt", "3071.20 Melk 3", "-1000.00"),
            (f"{EARLIER}-06-15", "Melk solgt", "2700 Utgående mva", "-250.00"),
        ],
    ),
    ["Meieriet AS", "Avregning juni", f"Org.nr. {OTHER_ORGNR}", "Å betale 1 250,00"],
    cover(
        "1",
        Y,
        [
            (f"{Y}-01-10", "Faktura 77 fra Landhandelen SA", "4020.20 Kraftfôr 1", "800.00"),
            (f"{Y}-01-10", "Faktura 77 fra Landhandelen SA", "2710 Inngående mva", "200.00"),
            (f"{Y}-01-10", "Faktura 77 fra Landhandelen SA", "2400 Leverandørgjeld", "-1000.00"),
            (f"{Y}-01-12", "Betaling", "2400 Leverandørgjeld", "1000.00"),
            (f"{Y}-01-12", "Betaling", "2390.10 Driftskreditt", "-1000.00"),
        ],
        sum_debit="2 000,00",
    ),
    ["Landhandelen SA", "Faktura 77", "Kraftfôr 800,00"],
    SCAN,
    cover(
        "2",
        Y,
        [
            (f"{Y}-02-05", "Avregning januar", "1500 Kundefordringer", "1250.00"),
            (f"{Y}-02-05", "Avregning januar", "3071.20 Melk 3", "-1000.00"),
            (f"{Y}-02-05", "Avregning januar", "2700 Utgående mva", "-250.00"),
        ],
    ),
    cover(
        "3",
        Y,
        [
            (f"{Y}-02-20", "Gjerdemateriell", "4020.20 Kraftfôr 11", "100.00"),
            (f"{Y}-02-20", "Gjerdemateriell", "2711 Inngående mva", "15.00"),
            (f"{Y}-02-20", "Gjerdemateriell", "2400 Leverandørgjeld", "-115.00"),
        ],
        sum_debit="999,00",
    ),
    ["Felleskjøpet", "Gjerdemateriell 115,00"],
    cover("777", Y, [(f"{Y}-02-25", "Kontantkjøp", "6540 Inventar", "50.00")]),
    ["Kvittering 50,00"],
]


def the_printout() -> bytes:
    return pages_pdf(PRINTOUT_PAGES)


# --- Talking to the API ---------------------------------------------------------------------


def _upload(client: TestClient, company_id: int, *files: tuple[str, bytes]) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/vedlegg",
        files=[("files", (name, content, "application/pdf")) for name, content in files],
    )


def _uploaded(client: TestClient, company_id: int, *files: tuple[str, bytes]) -> dict[str, Any]:
    response = _upload(client, company_id, *files)
    assert response.status_code == 202, response.text
    return _get(client, company_id, response.json()["id"])


def _get(client: TestClient, company_id: int, import_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/saft/vedlegg/{import_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _archive(client: TestClient, company_id: int, import_id: int) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/saft/vedlegg/{import_id}/arkivering"
    )


def _codes(review: dict[str, Any]) -> set[str]:
    return {finding["code"] for finding in review["findings"]}


def _books_brought_over(client: TestClient) -> dict[str, Any]:
    """Books with both years brought over from Tripletex, as the import posts them."""
    company = _books(client)
    read = _both_years(client, company["id"])
    assert _post_import(client, company["id"], read["id"]).status_code == 202
    assert _get_import(client, company["id"], read["id"])["status"] == "posted"
    return company


def _vouchers(client: TestClient, company_id: int, year: int) -> dict[str, dict[str, Any]]:
    page = client.get(
        f"/api/v1/companies/{company_id}/bilag", params={"aar": year, "journal": "IMPORT"}
    ).json()
    return {voucher["voucher_no"]: voucher for voucher in page["vouchers"]}


# --- The whole way -----------------------------------------------------------------------


def test_each_document_goes_on_the_voucher_with_its_number(client: TestClient) -> None:
    company = _books_brought_over(client)
    read = _uploaded(client, company["id"], ("Bilag_2026.pdf", the_printout()))
    assert read["status"] == "ready", read
    review = read["review"]
    assert review["kind"] == "vedlegg"
    assert review["software"] == "Tripletex"
    assert review["files"] == [
        {"file_name": "Bilag_2026.pdf", "pages": 10, "vouchers": 5, "loose_pages": 0}
    ]
    actions = {voucher["number"]: voucher["action"] for voucher in review["vouchers"]}
    assert actions == {
        f"17-{EARLIER}": "attach",
        f"1-{Y}": "attach",
        f"2-{Y}": "no_attachments",
        f"3-{Y}": "attach",
        f"777-{Y}": "not_in_books",
    }
    # Voucher 1 was split by date when it came over, and goes on both parts.
    by_number = {voucher["number"]: voucher for voucher in review["vouchers"]}
    assert [t["voucher_no"] for t in by_number[f"1-{Y}"]["targets"]] == [f"{Y}-1", f"{Y}-1-0112"]
    assert (review["to_attach"], review["documents"]) == (3, 4)
    assert (review["no_attachments"], review["not_in_books"]) == (1, 1)
    # The cover's debit total, read line by line where Tripletex prints no sum.
    assert by_number[f"17-{EARLIER}"]["printed_debit"] == "1250.00"
    assert by_number[f"17-{EARLIER}"]["booked_debit"] == "1250.00"
    assert by_number[f"1-{Y}"]["printed_debit"] == "2000.00"
    assert by_number[f"1-{Y}"]["booked_debit"] == "2000.00"
    assert by_number[f"1-{Y}"]["attachment_pages"] == 2
    assert {"belop_avviker", "bilag_finnes_ikke", "uten_vedlegg", "bilag_delt_paa_dato"} <= (
        _codes(review)
    )
    assert review["postable"] is True
    assert read["may_post"] is True
    # A preview: nothing is in the archive yet.
    before = _vouchers(client, company["id"], Y)
    assert all(voucher["documents"] == [] for voucher in before.values())

    archived = _archive(client, company["id"], read["id"])
    assert archived.status_code == 202, archived.text
    done = _get(client, company["id"], read["id"])
    assert done["status"] == "posted", done
    assert done["files_kept"] is False
    assert done["result"]["documents"] == 4

    vouchers = _vouchers(client, company["id"], Y)
    for number in (f"{Y}-1", f"{Y}-1-0112"):
        documents = vouchers[number]["documents"]
        assert [d["filename"] for d in documents] == [f"Tripletex bilag 1-{Y}.pdf"]
        assert documents[0]["content_type"] == "application/pdf"
    assert vouchers[f"{Y}-2"]["documents"] == []
    assert len(vouchers[f"{Y}-3"]["documents"]) == 1
    earlier = _vouchers(client, company["id"], EARLIER)
    assert len(earlier[f"{EARLIER}-17"]["documents"]) == 1
    # «Se bilagene» opens these alone: a year of vouchers is pages long, and the
    # pilot looked for September's documents on the first two (2026-10-22).
    with_documents = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": Y, "med_dokument": "true"}
    ).json()
    assert [voucher["voucher_no"] for voucher in with_documents["vouchers"]] == [
        f"{Y}-1",
        f"{Y}-1-0112",
        f"{Y}-3",
    ]
    assert with_documents["total_count"] == 3

    # The document is Tripletex's page and the two after it, as printed.
    document = vouchers[f"{Y}-1"]["documents"][0]
    downloaded = client.get(f"/api/v1/companies/{company['id']}/dokument/{document['id']}")
    assert downloaded.status_code == 200
    assert len(PdfReader(BytesIO(downloaded.content)).pages) == 3
    assert hashlib.sha256(downloaded.content).hexdigest() == by_number[f"1-{Y}"]["sha256"]

    # The same printout again has nothing new, and archives nothing twice.
    again = _uploaded(client, company["id"], ("Bilag_2026.pdf", the_printout()))
    assert again["review"]["postable"] is False
    assert again["review"]["already_attached"] == 3
    assert {"allerede_i_arkivet", "ingenting_aa_legge_paa"} <= _codes(again["review"])
    assert again["may_post"] is False
    refused = _archive(client, company["id"], again["id"])
    assert refused.status_code == 422
    assert refused.json()["code"] == "import_not_postable"


def test_the_archiving_stops_when_the_archive_changed_since_the_review(
    client: TestClient,
) -> None:
    company = _books_brought_over(client)
    read = _uploaded(client, company["id"], ("Bilag.pdf", the_printout()))
    assert read["review"]["documents"] == 4

    # Somebody puts one part's document on by hand in the meantime.
    found = printout.read(the_printout(), name="Bilag.pdf")
    first = next(v for v in found.vouchers if v.label == f"1-{Y}")
    [(_, document)] = printout.cut(the_printout(), name="Bilag.pdf", vouchers=[first])
    part = _vouchers(client, company["id"], Y)[f"{Y}-1"]
    by_hand = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{part['id']}/dokument",
        files={"file": ("bilag.pdf", document, "application/pdf")},
    )
    assert by_hand.status_code == 201, by_hand.text

    assert _archive(client, company["id"], read["id"]).status_code == 202
    stopped = _get(client, company["id"], read["id"])
    assert stopped["status"] == "ready", stopped
    assert stopped["error_code"] == "review_changed"
    # Nothing was put on, and the new review leaves out the part that has it.
    assert stopped["review"]["documents"] == 3
    assert len(_vouchers(client, company["id"], Y)[f"{Y}-1-0112"]["documents"]) == 0

    assert _archive(client, company["id"], read["id"]).status_code == 202
    assert _get(client, company["id"], read["id"])["status"] == "posted"
    vouchers = _vouchers(client, company["id"], Y)
    assert len(vouchers[f"{Y}-1"]["documents"]) == 1
    assert len(vouchers[f"{Y}-1-0112"]["documents"]) == 1


def test_a_key_reads_at_once_and_waits_for_the_person_to_archive(client: TestClient) -> None:
    """Reading is a preview a key may make itself; the archive is for good, so it asks.

    The card names the upload from what Myntos read, not from the file's name,
    which the key chose.
    """
    company = _books_brought_over(client)
    header = agent_header(
        client, company["id"], scopes=("read", "write"), approval_scopes=("post",)
    )
    made = client.post(
        f"/api/v1/companies/{company['id']}/saft/vedlegg",
        files=[("files", ("Godkjenn dette.pdf", the_printout(), "application/pdf"))],
        headers=header,
    )
    assert made.status_code == 202, made.text
    assert _get(client, company["id"], made.json()["id"])["status"] == "ready"

    held = client.post(
        f"/api/v1/companies/{company['id']}/saft/vedlegg/{made.json()['id']}/arkivering",
        headers=header,
    )
    assert held.status_code == 202, held.text
    approval_id = held.json()["approval_id"]
    base = f"/api/v1/companies/{company['id']}/godkjenninger/{approval_id}"
    shown = client.get(base).json()
    assert shown["target"] == [
        f"Utskriften av bilag fra Tripletex (opplasting {made.json()['id']}): 4 dokumenter "
        "fra 3 bilag legges i bilagsarkivet"
    ]
    # Nothing is in the archive while it waits.
    assert _vouchers(client, company["id"], Y)[f"{Y}-3"]["documents"] == []

    approved = write(client, "POST", f"{base}/godkjenn", json={"files": []})
    assert approved.status_code == 200, approved.text
    assert _get(client, company["id"], made.json()["id"])["status"] == "posted"
    assert len(_vouchers(client, company["id"], Y)[f"{Y}-3"]["documents"]) == 1


# --- What is refused -------------------------------------------------------------------------


def test_another_companys_printout_is_refused_whole(client: TestClient) -> None:
    company = _books_brought_over(client)
    theirs = pages_pdf(
        [
            cover("17", EARLIER, [(f"{EARLIER}-06-15", "Melk", "1920", "1.00")], orgnr=OTHER_ORGNR),
            ["Faktura"],
        ]
    )
    read = _uploaded(client, company["id"], ("andre.pdf", theirs))
    assert read["status"] == "failed"
    assert read["error_code"] == "printout_wrong_company"
    assert OTHER_ORGNR in read["error"]
    assert read["review"] is None


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(text_pdf(INVOICE_LINES), id="an ordinary invoice"),
        pytest.param(b"Bilag nummer 1-2026", id="not a PDF"),
    ],
)
def test_a_file_that_is_not_the_printout_is_refused(client: TestClient, content: bytes) -> None:
    company = _books_brought_over(client)
    read = _uploaded(client, company["id"], ("faktura.pdf", content))
    assert read["status"] == "failed"
    assert read["error_code"] == "invalid_printout"


def test_an_empty_file_is_refused_before_it_is_kept(client: TestClient) -> None:
    company = _books(client)
    response = _upload(client, company["id"], ("tom.pdf", b""))
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_printout"


def test_the_saft_imports_and_the_printouts_are_kept_apart(client: TestClient) -> None:
    company = _books(client)
    saft = _both_years(client, company["id"])
    uploaded = _uploaded(client, company["id"], ("Bilag.pdf", the_printout()))
    base = f"/api/v1/companies/{company['id']}/saft"
    assert [job["id"] for job in client.get(f"{base}/innlesing").json()] == [saft["id"]]
    assert [job["id"] for job in client.get(f"{base}/vedlegg").json()] == [uploaded["id"]]
    assert client.get(f"{base}/innlesing/{uploaded['id']}").status_code == 404
    assert client.get(f"{base}/vedlegg/{saft['id']}").status_code == 404
    assert write(client, "DELETE", f"{base}/innlesing/{uploaded['id']}").status_code == 404
    # Before the vouchers are in the books, nothing has a voucher to go on.
    assert uploaded["review"]["not_in_books"] == 4
    assert uploaded["review"]["postable"] is False
    discarded = write(client, "DELETE", f"{base}/vedlegg/{uploaded['id']}")
    assert discarded.status_code == 204
    assert client.get(f"{base}/vedlegg").json() == []


def test_a_reader_sees_the_uploads_but_cannot_make_one(client: TestClient) -> None:
    company = _books(client)
    join_company(client, company["id"], email="leser@example.no", role="viewer")
    assert _upload(client, company["id"], ("Bilag.pdf", the_printout())).status_code == 403
    assert client.get(f"/api/v1/companies/{company['id']}/saft/vedlegg").status_code == 200


# --- Reading the printout ------------------------------------------------------------------


def test_a_cover_running_onto_a_second_page_stays_the_cover() -> None:
    first = cover(
        "5",
        Y,
        [(f"{Y}-03-01", "Lønn", "5000 Lønn", "30000.00")]
        + [(f"{Y}-03-01", "Lønn", "1920 Bank", "-10000.00")] * 2,
    )
    rest = [
        f"Bilag nummer 5-{Y} Bilag nummer 5-{Y}",
        f"{Y}-03-01 Lønn 1920 Bank {nok('-10000.00')}",
    ]
    found = printout.read(pages_pdf([first, rest, ["Lønnsslipp"]]), name="lønn.pdf")
    [voucher] = found.vouchers
    assert (voucher.pages, voucher.attachment_pages) == (3, 1)
    assert voucher.debit == Decimal("30000.00")
    assert voucher.on is not None and voucher.on.isoformat() == f"{Y}-03-01"


def test_pages_before_the_first_cover_belong_to_no_voucher() -> None:
    found = printout.read(pages_pdf([["Forside"], *PRINTOUT_PAGES[:2]]), name="b.pdf")
    assert found.loose_pages == 1
    assert [v.first_page for v in found.vouchers] == [1]


def test_the_same_header_after_an_attachment_is_an_attachment() -> None:
    """A printed voucher somebody attached to its own voucher is a document, not a new voucher."""
    pages = [*PRINTOUT_PAGES[:2], PRINTOUT_PAGES[0]]
    [voucher] = printout.read(pages_pdf(pages), name="b.pdf").vouchers
    assert voucher.attachment_pages == 2


def test_a_document_is_cut_the_same_way_every_time() -> None:
    content = the_printout()
    found = printout.read(content, name="b.pdf")
    with_documents = [v for v in found.vouchers if v.sha256 is not None]
    assert [v.label for v in with_documents] == [f"17-{EARLIER}", f"1-{Y}", f"3-{Y}", f"777-{Y}"]
    pieces = printout.cut(content, name="b.pdf", vouchers=list(reversed(with_documents)))
    assert all(hashlib.sha256(piece).hexdigest() == v.sha256 for v, piece in pieces)
    # Another printout's pages are not the documents the review named.
    other = pages_pdf([*PRINTOUT_PAGES[:1], ["Et annet vedlegg"], *PRINTOUT_PAGES[2:]])
    with pytest.raises(InvalidPrintoutError):
        printout.cut(other, name="b.pdf", vouchers=with_documents[:1])
