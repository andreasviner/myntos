"""The year so far from another program's SAF-T file, and the two compared.

The file is the synthetic Tripletex-shaped one from ``test_saft_import``, with a
quarter of vouchers added in the shapes the farm pilot's real file had (it stays
out of the repository, in regnaskaps_tests/): an invoice whose payment sits in
the same voucher on a later day, a sale to a customer the opening balance never
named, input VAT at 15 % on Tripletex's own 2711, and a filed term settled onto
2740 with the øre rounded off on 7740.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.saft import reader
from myntos.saft.errors import InvalidSaftFileError
from myntos.saft.voucher_import import (
    PlannedLine,
    _settles_vat,
    _shared,
    split_code,
    vat_as_kept,
    vat_home,
    vat_parts,
)
from tests.conftest import write
from tests.test_arsoppgjor_api import LAST_YEAR, _books
from tests.test_cashflow_api import record
from tests.test_saft_import import ACCOUNTS, Account, _create_missing, saft_file, upload

Y = LAST_YEAR


def _line(
    record_id: str,
    account: str,
    amount: str,
    on: str,
    *,
    text: str = "",
    supplier: str | None = None,
    customer: str | None = None,
    tax: tuple[str, str, str] | None = None,
) -> str:
    value = Decimal(amount)
    side = "Debit" if value >= 0 else "Credit"
    party = (
        f"<SupplierID>{supplier}</SupplierID>"
        if supplier
        else f"<CustomerID>{customer}</CustomerID>"
        if customer
        else ""
    )
    taxed = ""
    if tax is not None:
        code, base, vat = tax
        taxed = (
            f"<TaxInformation><TaxType>MVA</TaxType><TaxCode>{code}</TaxCode>"
            f"<TaxPercentage>{'15' if code == '11' else '25'}</TaxPercentage>"
            f"<TaxBase>{base}</TaxBase><{side}TaxAmount><Amount>{vat}</Amount>"
            f"</{side}TaxAmount></TaxInformation>"
        )
    return (
        f"<Line><RecordID>{record_id}</RecordID><AccountID>{account}</AccountID>"
        f"<ValueDate>{Y}-{on}</ValueDate>{party}<Description>{text}</Description>"
        f"<{side}Amount><Amount>{abs(value):.2f}</Amount></{side}Amount>{taxed}</Line>"
    )


def _voucher(number: str, on: str, text: str, kind: str, lines: list[str]) -> str:
    return (
        f"<Transaction><TransactionID>{number}</TransactionID><Period>{int(on[:2])}</Period>"
        f"<PeriodYear>{Y}</PeriodYear><TransactionDate>{Y}-{on}</TransactionDate>"
        f"<TransactionType>Normal</TransactionType><Description>{text}</Description>"
        f"<VoucherType>{kind}</VoucherType><SystemEntryDate>{Y}-{on}</SystemEntryDate>"
        f"<GLPostingDate>{Y}-{on}</GLPostingDate>{''.join(lines)}</Transaction>"
    )


VOUCHERS = [
    # Feed bought on 10 January and paid from the driftskreditt on the 12th,
    # in one voucher, as Tripletex keeps them.
    _voucher(
        "1",
        "01-10",
        "Faktura 77 fra Landhandelen SA",
        "Leverandørfaktura",
        [
            _line("11", "402020", "800.00", "01-10", tax=("1", "800.00", "200.00")),
            _line("12", "2710", "200.00", "01-10"),
            _line("13", "2400", "-1000.00", "01-10", supplier="20001"),
            _line("14", "2400", "1000.00", "01-12", supplier="20001", text="Betaling"),
            _line("15", "239010", "-1000.00", "01-12", text="Betaling"),
        ],
    ),
    # Milk sold to a dairy the opening balance had no open item with.
    _voucher(
        "2",
        "02-05",
        "Avregning januar",
        "Landbrukets Dataflyt",
        [
            _line("21", "1500", "1250.00", "02-05", customer="10003"),
            _line("22", "307120", "-1000.00", "02-05", tax=("3", "1000.00", "250.00")),
            _line("23", "2700", "-250.00", "02-05"),
        ],
    ),
    # Fencing at 15 %, on the rate's own input account.
    _voucher(
        "3",
        "02-20",
        "Gjerdemateriell",
        "Leverandørfaktura",
        [
            _line("31", "402020", "100.00", "02-20", tax=("11", "100.00", "15.00")),
            _line("32", "2711", "15.00", "02-20"),
            _line("33", "2400", "-115.00", "02-20", supplier="20001"),
        ],
    ),
    # The term settled when Tripletex filed it, rounded to whole kroner.
    _voucher(
        "500046",
        "02-28",
        "Mva-melding, Januar - Februar",
        "Mva-melding",
        [
            _line("41", "2700", "250.00", "02-28"),
            _line("42", "2710", "-200.00", "02-28"),
            _line("43", "2711", "-15.00", "02-28"),
            _line("44", "2740", "-35.00", "02-28"),
        ],
    ),
]

#: Every debit line above, as the file's own TotalDebit states it.
TOTAL_DEBIT = sum(
    (
        Decimal(amount)
        for amount in ("800.00", "200.00", "1000.00", "1250.00", "100.00", "15.00", "250.00")
    ),
    Decimal(0),
)

EXTRA_ACCOUNTS: list[Account] = [
    ("402020", "Kraftfôr storfe", "varekostnad", "4005", "0", "0"),
    ("307120", "Melk", "salgsinntekt", "3000", "0", "0"),
    ("2700", "Utgående merverdiavgift", "kortsiktigGjeld", "2740", "0", "0"),
    ("2711", "Inngående mva, middels sats", "kortsiktigGjeld", "2740", "0", "0"),
    ("2740", "Oppgjørskonto mva", "kortsiktigGjeld", "2740", "0", "0"),
]
CUSTOMERS = [
    ("10001", "Meieriet AS", "910000020", "1500", "20000"),
    ("10002", "Slakteriet SA", "910000039", "1500", "10000"),
]
NEW_CUSTOMER = (
    "<Customer><RegistrationNumber>910000055</RegistrationNumber><Name>Bygdemeieriet AS</Name>"
    "<CustomerID>10003</CustomerID></Customer>"
)


def entries(vouchers: list[str] | None = None, *, number: int | None = None) -> str:
    chosen = VOUCHERS if vouchers is None else vouchers
    return (
        f"<GeneralLedgerEntries><NumberOfEntries>{number or len(chosen)}</NumberOfEntries>"
        f"<TotalDebit>{TOTAL_DEBIT if vouchers is None else 0}</TotalDebit>"
        f"<TotalCredit>{TOTAL_DEBIT if vouchers is None else 0}</TotalCredit>"
        f"<Journal><JournalID>GL</JournalID><Description>Hovedbok</Description><Type>GL</Type>"
        f"{''.join(chosen)}</Journal></GeneralLedgerEntries>"
    )


def year_file(**kwargs: Any) -> bytes:
    content = saft_file(accounts=ACCOUNTS + EXTRA_ACCOUNTS, after_master=entries(), **kwargs)
    # The customer first met this year is in the master data with no balance.
    return content.replace(b"</Customers>", NEW_CUSTOMER.encode() + b"</Customers>")


def _bilag(client: TestClient, company_id: int, content: bytes, *, til: str | None = None) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/bilag/kontroll",
        files={"file": ("saft.xml", content, "application/xml")},
        data={"til": til} if til else None,
    )


def _post(client: TestClient, company_id: int, content: bytes, *, til: str | None = None) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/bilag",
        files={"file": ("saft.xml", content, "application/xml")},
        data={"til": til} if til else None,
    )


def _compare(client: TestClient, company_id: int, content: bytes) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/sammenligning",
        files={"file": ("saft.xml", content, "application/xml")},
    )


def _codes(body: dict[str, Any]) -> set[str]:
    return {finding["code"] for finding in body["findings"]}


def _opening(client: TestClient, company_id: int, content: bytes, **move: str) -> None:
    """The opening balance from ``content``, with any line moved as ``move`` says."""
    first = upload(client, company_id, content).json()
    _create_missing(client, company_id, first)
    second = upload(client, company_id, content).json()
    lines = []
    for line in second["lines"]:
        account = line["account_number"]
        source = (line["description"] or "").split(" ", 1)[0]
        lines.append(
            {
                "account_number": move.get(source, account),
                "debit": line["debit"],
                "credit": line["credit"],
                "party_id": line["party_id"],
                "description": line["description"],
            }
        )
    posted = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/inngaende-balanse",
        json={"lines": lines},
    )
    assert posted.status_code == 201, posted.text


# --- The whole move ---------------------------------------------------------------------


def test_the_years_vouchers_come_over_and_the_books_then_agree(client: TestClient) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content)

    first = _bilag(client, company["id"], content)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["postable"] is False
    assert {"kontoer_mangler", "parter_mangler"} <= _codes(body)
    # The four digits are the account, and 2711 is input VAT, on 2710.
    assert {add["number"] for add in body["accounts_to_add"]} >= {"4020", "3071"}
    assert "2711" not in {add["number"] for add in body["accounts_to_add"]}
    assert [add["name"] for add in body["parties_to_add"]] == ["Bygdemeieriet AS"]
    assert {"bilag_delt_paa_dato", "mva_underkonto", "mva_meldinger_sendt"} <= _codes(body)

    _create_missing(client, company["id"], body)
    ready = _bilag(client, company["id"], content).json()
    assert ready["postable"] is True, ready["findings"]
    # Voucher 1 is two: the invoice on the 10th and its payment on the 12th.
    assert ready["vouchers"] == 5
    assert [(m["month"], m["vouchers"]) for m in ready["months"]] == [(1, 2), (2, 3)]

    posted = _post(client, company["id"], content)
    assert posted.status_code == 201, posted.text
    result = posted.json()
    assert result["posted"] == 5
    assert result["journal"] == "IMPORT"
    assert (result["first_voucher_no"], result["last_voucher_no"]) == (f"{Y}-1", f"{Y}-500046")

    # Read again, the same file has nothing new, and posting it is refused.
    again = _bilag(client, company["id"], content).json()
    assert (again["vouchers"], again["already_imported"]) == (0, 5)
    assert "ingen_bilag" in _codes(again)
    twice = _post(client, company["id"], content)
    assert twice.status_code == 422
    assert twice.json()["code"] == "invalid_voucher_import"

    # And the two programs agree, account, party and VAT term alike.
    compared = _compare(client, company["id"], content)
    assert compared.status_code == 200, compared.text
    verdict = compared.json()
    assert verdict["matches"] is True, verdict
    assert verdict["through"] == f"{Y}-02-28"


def test_imported_vouchers_stay_out_of_the_register_but_count_for_vat(
    client: TestClient,
) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    assert _post(client, company["id"], content).status_code == 201

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed == []
    period = client.get(f"/api/v1/companies/{company['id']}/mva/termin?aar={Y}&termin=1").json()
    assert Decimal(period["output_vat"]) == Decimal("250.00")
    assert Decimal(period["input_vat"]) == Decimal("215.00")
    # Tripletex's settlement is a settlement here too, so the accounts still
    # reconcile with the return instead of reading as settled to nothing.
    assert Decimal(period["reconciliation"]["difference"]) == Decimal("0.00")


def test_the_bank_follows_the_opening_balance_when_it_was_moved(client: TestClient) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content, **{"239010": "1920"})
    body = _bilag(client, company["id"], content).json()
    assert "konto_som_inngaende_balanse" in _codes(body)
    _create_missing(client, company["id"], body)
    assert _post(client, company["id"], content).status_code == 201
    compared = _compare(client, company["id"], content).json()
    assert compared["matches"] is True, compared


def test_what_one_side_lacks_is_found_by_the_comparison(client: TestClient) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    assert _post(client, company["id"], content, til=f"{Y}-02-10").status_code == 201
    # The fencing on the 20th never came over: the cost, the supplier, the VAT
    # account and the 15 % code all part in February, and nothing else does.
    verdict = _compare(client, company["id"], content).json()
    assert verdict["matches"] is False
    assert {(a["account_number"], tuple(a["months"])) for a in verdict["accounts"]} == {
        ("2400", (2,)),
        ("2740", (2,)),
        ("4020", (2,)),
    }
    assert [(p["name"], p["difference"]) for p in verdict["parties"]] == [
        ("Landhandelen SA", "115.00")
    ]
    assert [(v["code"], v["vat_in_file"], v["vat_in_myntos"]) for v in verdict["vat"]] == [
        ("11", "15.00", "0.00")
    ]
    # The settlement on the 28th moves VAT between VAT accounts, which are one
    # row, so leaving it out is not a difference.
    assert verdict["through"] == f"{Y}-02-28"


# --- What stops it -----------------------------------------------------------------------


def test_nothing_comes_over_before_the_opening_balance(client: TestClient) -> None:
    company = _books(client)
    body = _bilag(client, company["id"], year_file()).json()
    assert "inngaende_balanse_mangler" in _codes(body)
    refused = _post(client, company["id"], year_file())
    assert refused.status_code == 422
    assert "inngaende_balanse_mangler" in {f["code"] for f in refused.json()["findings"]}


def test_vouchers_already_in_myntos_in_the_period_stop_it(client: TestClient) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    entry = record(
        client,
        company["id"],
        type_code="datakostnad",
        gross_amount="125.00",
        occurred_on=date(Y, 2, 10).isoformat(),
        tax_code="1",
    )
    assert entry.status_code == 201, entry.text
    body = _bilag(client, company["id"], content).json()
    assert "bilag_i_perioden" in _codes(body)
    assert body["postable"] is False
    # Up to the day before, it may.
    earlier = _bilag(client, company["id"], content, til=f"{Y}-02-09").json()
    assert "bilag_i_perioden" not in _codes(earlier)
    assert earlier["postable"] is True
    assert earlier["later"] == 2


def test_another_companys_file_or_another_year_is_refused(client: TestClient) -> None:
    company = _books(client)
    other = _bilag(client, company["id"], year_file(orgnr="910000012"))
    assert other.status_code == 422
    assert other.json()["code"] == "saft_wrong_company"
    old = saft_file(year=Y - 1, accounts=ACCOUNTS, after_master=entries([]))
    wrong = _bilag(client, company["id"], old)
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "saft_wrong_year"
    assert _compare(client, company["id"], year_file(orgnr="910000012")).status_code == 422


def test_an_imported_voucher_is_not_reversed_from_the_register(client: TestClient) -> None:
    company = _books(client)
    content = year_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    assert _post(client, company["id"], content).status_code == 201
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    # The opening voucher is the first; the import's follow it.
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{state['standing']['id'] + 1}/reversal",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cannot_reverse_here"


# --- Reading the entries -----------------------------------------------------------------


def test_the_entries_are_read_split_by_date_and_counted() -> None:
    found = reader.read_entries_bytes(year_file())
    assert len(found.transactions) == 4
    first = found.transactions[0]
    assert {line.on for line in first.lines} == {date(Y, 1, 10), date(Y, 1, 12)}
    assert first.lines[0].taxes[0].code == "1"
    assert "Bygdemeieriet AS" in {party.name for party in found.balances.register}


def test_a_file_cut_short_is_refused() -> None:
    short = saft_file(accounts=ACCOUNTS, after_master=entries(VOUCHERS[:1], number=4))
    with pytest.raises(InvalidSaftFileError, match="avkortet"):
        reader.read_entries_bytes(short)


def test_a_file_in_parts_is_refused_for_the_vouchers() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SAF-T Financial_910000004_20260101_1_2.xml", year_file())
        archive.writestr("SAF-T Financial_910000004_20260101_2_2.xml", year_file())
    with pytest.raises(InvalidSaftFileError, match="delt i 2 deler"):
        reader.read_entries_bytes(buffer.getvalue())
    # The balances alone can still be read from the first part.
    assert reader.read_bytes(buffer.getvalue()).orgnr == "910000004"


def test_tripletex_codes_and_vat_accounts() -> None:
    assert split_code("UTTAK-3") == ("3", "uttak")
    assert split_code("TAP-1") == ("1", "tapPåKrav")
    assert split_code("1") == ("1", None)
    assert (vat_home("2711"), vat_home("2703"), vat_home("2740")) == ("2710", "2700", "2740")

    def line(account: str, debit: str = "0", credit: str = "0") -> PlannedLine:
        return PlannedLine(
            record_id="1",
            account_number=account,
            description="",
            debit=Decimal(debit),
            credit=Decimal(credit),
            party_pk=None,
        )

    vat = {"2700", "2710", "2740"}
    settlement = [line("2700", debit="250"), line("2710", credit="215"), line("2740", credit="35")]
    assert _settles_vat(settlement, vat)
    rounded = [*settlement[:2], line("2740", credit="34.85"), line("7740", credit="0.15")]
    assert _settles_vat(rounded, vat)
    # Fiken's: each post of the old melding in whole kroner, so 1.43 over two input lines.
    fiken = [
        line("2700", debit="12500"),
        line("2710", credit="223.50"),
        line("2710", credit="70.93"),
        line("2740", credit="12207"),
        line("7740", debit="1.43"),
    ]
    assert _settles_vat(fiken, vat)
    # More than a krone a line is not rounding.
    too_much = [*settlement[:2], line("2740", credit="32"), line("7740", credit="3")]
    assert not _settles_vat(too_much, vat)
    payment = [line("2740", debit="35"), line("2390", credit="35")]
    assert not _settles_vat(payment, vat)


# --- A line with several VAT codes -------------------------------------------------------


def _taxed(amount: str, *taxes: tuple[str, str, str]) -> reader.SourceLine:
    """A line of ``amount`` carrying each (code, base, VAT) on its own side."""
    value = Decimal(amount)
    return reader.SourceLine(
        record_id="7",
        account_id="3000",
        description="",
        amount=value,
        on=date(Y, 3, 1),
        party_kind=None,
        party_id=None,
        taxes=tuple(
            reader.SourceTax(
                code=code,
                percentage=Decimal(25),
                base=Decimal(base),
                debit_tax=Decimal(vat) if value > 0 else Decimal(0),
                credit_tax=Decimal(0) if value > 0 else Decimal(vat),
            )
            for code, base, vat in taxes
        ),
    )


def _amounts(parts: list[tuple[Decimal, reader.SourceTax | None]] | None) -> list[str] | None:
    return None if parts is None else [f"{amount}" for amount, _ in parts]


def test_each_code_gets_its_base_of_the_line() -> None:
    sale = _taxed("-1000.00", ("3", "600.00", "150.00"), ("31", "400.00", "60.00"))
    parts = vat_parts(sale)
    assert _amounts(parts) == ["-600.00", "-400.00"]
    assert parts is not None
    assert [tax.code if tax else None for _, tax in parts] == ["3", "31"]
    # The tax information is the file's, untouched.
    assert parts[1][1] == sale.taxes[1]
    # A purchase is the same, on the debit side.
    bought = _taxed("1000.00", ("1", "600.00", "150.00"), ("11", "400.00", "60.00"))
    assert _amounts(vat_parts(bought)) == ["600.00", "400.00"]
    # One code, or none, is the line as it is.
    assert _amounts(vat_parts(_taxed("-80.00", ("3", "64.00", "16.00")))) == ["-80.00"]
    assert vat_parts(_taxed("80.00")) == [(Decimal("80.00"), None)]


def test_what_the_bases_leave_goes_with_the_largest() -> None:
    # 100 of the line is under neither code: it stays on the line with the most.
    left = _taxed("-1000.00", ("3", "600.00", "150.00"), ("5", "300.00", "0.00"))
    assert _amounts(vat_parts(left)) == ["-700.00", "-300.00"]
    # Bases rounded an øre past the line give that øre back.
    rounded = _taxed("-100.00", ("3", "50.01", "12.50"), ("31", "50.00", "7.50"))
    assert _amounts(vat_parts(rounded)) == ["-50.00", "-50.00"]
    # An entry with no base and no VAT says nothing and is left out.
    empty = _taxed("-1000.00", ("3", "1000.00", "250.00"), ("0", "0.00", "0.00"))
    assert _amounts(vat_parts(empty)) == ["-1000.00"]


def test_codes_that_are_not_parts_of_the_line_are_refused() -> None:
    # Two codes each on the whole line: which part is which is not in the file.
    twice = _taxed("1000.00", ("1", "1000.00", "250.00"), ("11", "1000.00", "150.00"))
    assert vat_parts(twice) is None
    # VAT with no base would leave its code no part of the line.
    nothing = _taxed("100.00", ("1", "100.00", "25.00"), ("11", "0.00", "1.00"))
    assert vat_parts(nothing) is None


def _fiken(amount: str, vat: str, *, base: str = "0.00", code: str = "86") -> reader.SourceLine:
    """A cost line with its reverse charge as Fiken writes it: owed and deducted, no base."""
    owed = reader.SourceTax(code, Decimal(25), Decimal(base), Decimal(0), Decimal(vat))
    deducted = replace(owed, debit_tax=Decimal(vat), credit_tax=Decimal(0))
    return replace(_taxed(amount), taxes=(deducted, owed))


def test_a_reverse_charge_in_two_entries_is_one() -> None:
    # The purchase: the VAT on its side, and the line's amount as the base.
    bought = vat_as_kept(_fiken("768.96", "192.24"), {})
    assert bought.taxes == (
        reader.SourceTax(
            code="86",
            percentage=Decimal(25),
            base=Decimal("768.96"),
            debit_tax=Decimal("192.24"),
            credit_tax=Decimal(0),
        ),
    )
    assert vat_parts(bought) == [(Decimal("768.96"), bought.taxes[0])]
    # Its credit note: the same, on the credit side.
    refunded = vat_as_kept(_fiken("-768.96", "192.24"), {})
    assert [(t.base, t.debit_tax, t.credit_tax) for t in refunded.taxes] == [
        (Decimal("768.96"), Decimal(0), Decimal("192.24"))
    ]
    # A base the file gives is the base, whatever the line.
    given = vat_as_kept(_fiken("1000.00", "200.00", base="800.00"), {})
    assert given.taxes[0].base == Decimal("800.00")
    # The file's own name for the code is read through its code list.
    assert len(vat_as_kept(_fiken("100.00", "25.00", code="T86"), {"T86": "86"}).taxes) == 1


def test_a_reverse_charge_is_not_guessed_at() -> None:
    # No base, and VAT that is not the rate of the line: the base is unknown.
    odd = _fiken("1000.00", "200.00")
    assert vat_as_kept(odd, {}) is odd
    # The same shape on an ordinary code is not a reverse charge.
    ordinary = _fiken("1000.00", "250.00", code="1")
    assert vat_as_kept(ordinary, {}) is ordinary
    # Two entries owed, none deducted, is not one charge written twice.
    owed = _taxed("1000.00", ("86", "1000.00", "250.00"), ("86", "1000.00", "250.00"))
    assert vat_as_kept(owed, {}) is owed


def _as_fiken(amount: str, vat: str, *, code: str = "3", rate: int = 25) -> reader.SourceLine:
    """A line with its VAT as Fiken writes all of it: a debit, on a base of 0."""
    tax = reader.SourceTax(code, Decimal(rate), Decimal(0), Decimal(vat), Decimal(0))
    return replace(_taxed(amount), taxes=(tax,))


def test_fikens_vat_goes_the_way_its_line_goes() -> None:
    def kept(line: reader.SourceLine) -> tuple[str, str, str]:
        """Base, debit and credit VAT, to the øre."""
        (tax,) = vat_as_kept(line, {}).taxes
        return f"{tax.base:.2f}", f"{tax.debit_tax:.2f}", f"{tax.credit_tax:.2f}"

    # A sale is owed, on the line's own amount.
    assert kept(_as_fiken("-1000.00", "250.00")) == ("1000.00", "0.00", "250.00")
    # Its kreditnota, and a purchase, are debits already and only get the base.
    assert kept(_as_fiken("1000.00", "250.00")) == ("1000.00", "250.00", "0.00")
    assert kept(_as_fiken("800.00", "200.00", code="1")) == ("800.00", "200.00", "0.00")
    # At 0 % there is no VAT to move, and the base is still the line.
    assert kept(_as_fiken("-500.00", "0.00", code="5", rate=0)) == ("500.00", "0.00", "0.00")
    # As Tripletex writes it, nothing changes.
    tripletex = _taxed("-1000.00", ("3", "1000.00", "250.00"))
    assert vat_as_kept(tripletex, {}) is tripletex


def test_vat_is_not_moved_on_a_guess() -> None:
    # A spesifikasjon gives the direction itself.
    uttak = _as_fiken("-1000.00", "250.00", code="UTTAK-3")
    assert vat_as_kept(uttak, {}) is uttak
    # VAT that is not the rate of the line's amount is not about this line, so
    # the line's side says nothing about it, and neither does its amount.
    elsewhere = _as_fiken("-1250.00", "250.00")
    assert vat_as_kept(elsewhere, {}) is elsewhere
    # A negative amount was written that way on purpose.
    negative = _as_fiken("-1000.00", "-250.00")
    assert vat_as_kept(negative, {}) is negative


def test_a_currency_amount_is_shared_as_the_line_is() -> None:
    assert _shared(Decimal("-130.00"), [Decimal("-600.00"), Decimal("-400.00")]) == [
        Decimal("-78.00"),
        Decimal("-52.00"),
    ]
    # The øre the shares lose to rounding goes on the largest.
    thirds = _shared(Decimal("100.00"), [Decimal("3.00"), Decimal("1.00"), Decimal("1.00")])
    assert thirds == [Decimal("60.00"), Decimal("20.00"), Decimal("20.00")]
    split = _shared(Decimal("10.00"), [Decimal("1.00"), Decimal("1.00"), Decimal("1.00")])
    assert split == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]


def _with_tax(line: str, code: str, percentage: str, base: str, vat: str) -> str:
    """``line`` with one more ``TaxInformation``, on the side the line is on."""
    side = "Debit" if "<DebitAmount>" in line else "Credit"
    return line.replace(
        "</Line>",
        f"<TaxInformation><TaxType>MVA</TaxType><TaxCode>{code}</TaxCode>"
        f"<TaxPercentage>{percentage}</TaxPercentage><TaxBase>{base}</TaxBase>"
        f"<{side}TaxAmount><Amount>{vat}</Amount></{side}TaxAmount></TaxInformation></Line>",
    )


def _file_of(vouchers: list[str], total_debit: str, *, more: list[Account] | None = None) -> bytes:
    stated = entries(vouchers).replace(
        "<TotalDebit>0</TotalDebit>", f"<TotalDebit>{total_debit}</TotalDebit>"
    )
    content = saft_file(accounts=ACCOUNTS + EXTRA_ACCOUNTS + (more or []), after_master=stated)
    return content.replace(b"</Customers>", NEW_CUSTOMER.encode() + b"</Customers>")


#: The farm shop's February: food at 15 % and firewood at 25 % on one income
#: line, a sale with two rates as SAF-T allows it.
SHOP = _voucher(
    "2",
    "02-05",
    "Gårdsutsalg februar",
    "Salg",
    [
        _line("21", "1500", "1210.00", "02-05", customer="10003"),
        _with_tax(
            _line("22", "307120", "-1000.00", "02-05", tax=("3", "600.00", "150.00")),
            "31",
            "15",
            "400.00",
            "60.00",
        ),
        _line("23", "2700", "-210.00", "02-05"),
    ],
)


def test_a_line_with_two_codes_comes_over_as_a_line_per_code(client: TestClient) -> None:
    company = _books(client)
    # The feed (2 000 in debit), the shop (1 210) and the fencing (115).
    content = _file_of([VOUCHERS[0], SHOP, VOUCHERS[2]], "3325.00")
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert review["postable"] is True, review["findings"]
    assert "flere_mva_koder" not in _codes(review)
    shared = next(f for f in review["findings"] if f["code"] == "mva_koder_delt")
    assert shared["severity"] == "merknad"
    assert shared["message"].startswith("1 av linjene i filen har flere mva-koder")
    assert _post(client, company["id"], content).status_code == 201

    # Each rate is on the melding with the file's own base and VAT.
    period = client.get(f"/api/v1/companies/{company['id']}/mva/termin?aar={Y}&termin=1").json()
    sales = {
        line["mva_kode"]: (line["grunnlag"], line["merverdiavgift"])
        for line in period["lines"]
        if line["side"] == "utgaaende"
    }
    assert sales == {"3": ("600.00", "150.00"), "31": ("400.00", "60.00")}
    assert Decimal(period["output_vat"]) == Decimal("210.00")
    assert Decimal(period["reconciliation"]["difference"]) == Decimal("0.00")

    # And the two programs still agree, account, party and VAT code alike.
    verdict = _compare(client, company["id"], content).json()
    assert verdict["matches"] is True, verdict


def test_codes_whose_bases_pass_the_line_still_stop_the_import(client: TestClient) -> None:
    company = _books(client)
    # Two input codes, each on the whole of the line.
    mixed = _voucher(
        "5",
        "03-02",
        "Gjerde og fôr",
        "Leverandørfaktura",
        [
            _with_tax(
                _line("51", "402020", "1000.00", "03-02", tax=("1", "1000.00", "250.00")),
                "11",
                "15",
                "1000.00",
                "150.00",
            ),
            _line("52", "2710", "400.00", "03-02"),
            _line("53", "2400", "-1400.00", "03-02", supplier="20001"),
        ],
    )
    content = _file_of([mixed], "1400.00")
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert review["postable"] is False
    stop = next(f for f in review["findings"] if f["code"] == "flere_mva_koder")
    assert stop["severity"] == "feil"
    assert stop["message"].startswith("1 av linjene har flere mva-koder med grunnlag")
    assert "mva_koder_delt" not in _codes(review)
    assert _post(client, company["id"], content).status_code == 422


# --- Avdelinger and driftsgrener ---------------------------------------------------------

#: Tripletex's own three: «Avdeling» comes along, «Bilagsart» repeats the
#: voucher type and «Ansatte» names people, so those two stay behind.
ANALYSIS_TABLE = (
    "<AnalysisTypeTable>"
    + "".join(
        f"<AnalysisTypeTableEntry><AnalysisType>{kind}</AnalysisType>"
        f"<AnalysisTypeDescription>{kind_name}</AnalysisTypeDescription>"
        f"<AnalysisID>{value}</AnalysisID><AnalysisIDDescription>{name}</AnalysisIDDescription>"
        "</AnalysisTypeTableEntry>"
        for kind, kind_name, value, name in (
            ("A", "Avdeling", "20", "Jordbruk"),
            ("B", "Bilagsart", "1", "Leverandørfaktura"),
            ("E", "Ansatte", "7", "Kari Ansatt"),
        )
    )
    + "</AnalysisTypeTable>"
)


def _analysed_file(*, line_department: str | None = "20") -> bytes:
    """The year's file with the feed's cost line on avdeling 20, and two other dimensions."""
    marks = (
        "<Analysis><AnalysisType>B</AnalysisType><AnalysisID>1</AnalysisID></Analysis>"
        "<Analysis><AnalysisType>E</AnalysisType><AnalysisID>7</AnalysisID></Analysis>"
    )
    if line_department is not None:
        marks = (
            f"<Analysis><AnalysisType>A</AnalysisType><AnalysisID>{line_department}</AnalysisID>"
            "<DebitAnalysisAmount><Amount>800.00</Amount></DebitAnalysisAmount></Analysis>" + marks
        )
    content = year_file().replace(
        b"<AccountID>402020</AccountID><ValueDate>",
        f"<AccountID>402020</AccountID>{marks}<ValueDate>".encode(),
        1,
    )
    return content.replace(b"</MasterFiles>", ANALYSIS_TABLE.encode() + b"</MasterFiles>")


def _add_dimensions(client: TestClient, company_id: int, body: dict[str, Any]) -> None:
    """What the page does with ``dimensions_to_add``: the dimension first, then its values."""
    made: dict[str, int] = {}
    for add in body["dimensions_to_add"]:
        dimension_id = add["dimension_id"] or made.get(add["code"])
        if dimension_id is None:
            response = write(
                client,
                "POST",
                f"/api/v1/companies/{company_id}/dimensjoner",
                json={"code": add["code"], "description": add["dimension_description"]},
            )
            assert response.status_code == 201, response.text
            dimension_id = next(d["id"] for d in response.json() if d["code"] == add["code"])
            made[add["code"]] = dimension_id
        response = write(
            client,
            "POST",
            f"/api/v1/companies/{company_id}/dimensjoner/{dimension_id}/verdier",
            json={"value": add["value"], "description": add["description"]},
        )
        assert response.status_code == 201, response.text


def _department_result(client: TestClient, company_id: int) -> dict[str, str]:
    dimension = next(
        d
        for d in client.get(f"/api/v1/companies/{company_id}/dimensjoner").json()
        if d["kind"] == "AVDELING"
    )
    result = client.get(
        f"/api/v1/companies/{company_id}/dimensjoner/{dimension['id']}/resultat?aar={Y}"
    ).json()
    return {row["value"]: row["total"]["result"] for row in result["rows"]}


def test_a_lines_avdeling_comes_along_and_the_other_dimensions_stay(client: TestClient) -> None:
    company = _books(client)
    content = _analysed_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert review["postable"] is False
    assert {"dimensjoner_mangler", "dimensjoner_utelatt"} <= _codes(review)
    assert [
        (a["code"], a["dimension_description"], a["dimension_id"], a["value"], a["description"])
        for a in review["dimensions_to_add"]
    ] == [("AVDELING", "Avdeling", None, "20", "Jordbruk")]
    left = next(f for f in review["findings"] if f["code"] == "dimensjoner_utelatt")
    assert "«Ansatte»" in left["message"] and "«Bilagsart»" in left["message"]

    _add_dimensions(client, company["id"], review)
    ready = _bilag(client, company["id"], content).json()
    assert ready["postable"] is True, ready["findings"]
    assert _post(client, company["id"], content).status_code == 201
    # The feed's 800 is avdeling 20's cost, and nothing of the employee came over.
    assert _department_result(client, company["id"]) == {"20": "-800.00"}
    names = {
        value["description"]
        for d in client.get(f"/api/v1/companies/{company['id']}/dimensjoner").json()
        for value in d["values"]
    }
    assert names == {"Jordbruk"}


def test_on_a_farm_the_account_id_carries_the_avdeling(client: TestClient) -> None:
    company = _books(client)
    switched = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True}
    )
    assert switched.status_code == 200, switched.text
    # No line names its avdeling: the six-digit accounts say it (402020 and
    # 307120 are 20 Jordbruk, 239010 is 10 Finans).
    content = _analysed_file(line_department=None)
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert {(a["code"], a["value"]) for a in review["dimensions_to_add"]} == {
        ("AVDELING", "10"),
        ("AVDELING", "20"),
    }
    _add_dimensions(client, company["id"], review)
    assert _bilag(client, company["id"], content).json()["postable"] is True
    assert _post(client, company["id"], content).status_code == 201
    # Milk 1 000 less feed 800 and fencing 100, all of it jordbruk; Finans has
    # only the bank, which is no income or cost.
    assert _department_result(client, company["id"]) == {"10": "0.00", "20": "100.00"}


def test_outside_the_farming_add_on_six_digits_are_only_a_sub_account(
    client: TestClient,
) -> None:
    company = _books(client)
    content = _analysed_file(line_department=None)
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    review = _bilag(client, company["id"], content).json()
    assert review["dimensions_to_add"] == []
    assert review["postable"] is True, review["findings"]


def _reverse_charge_line(record_id: str, amount: str, on: str, vat: str) -> str:
    """Fiken's cost line for a service bought abroad: code 86 owed and deducted, base 0."""
    entries = "".join(
        f"<TaxInformation><TaxType>MVA</TaxType><TaxCode>86</TaxCode>"
        f"<TaxPercentage>25</TaxPercentage><TaxBase>0.00</TaxBase>"
        f"<{side}TaxAmount><Amount>{vat}</Amount></{side}TaxAmount></TaxInformation>"
        for side in ("Debit", "Credit")
    )
    return _line(record_id, "402020", amount, on).replace("</Line>", entries + "</Line>")


#: Fiken's own accounts for the two sides of a reverse charge.
REVERSE_CHARGE_ACCOUNTS: list[Account] = [
    ("2702", "Utgående mva, omvendt avgiftsplikt", "kortsiktigGjeld", "2740", "0", "0"),
    ("2712", "Inngående mva, omvendt avgiftsplikt", "kortsiktigGjeld", "2740", "0", "0"),
]


def test_a_reverse_charge_from_fiken_comes_over_owed_and_deducted(client: TestClient) -> None:
    company = _books(client)
    bought = _voucher(
        "22",
        "01-15",
        "Programvare fra utlandet",
        "Kjøp",
        [
            _line("61", "2702", "-200.00", "01-15"),
            _line("62", "2712", "200.00", "01-15"),
            _reverse_charge_line("63", "800.00", "01-15", "200.00"),
            _line("64", "239010", "-800.00", "01-15"),
        ],
    )
    # Its refund in the next term, the same shape on the other side.
    refunded = _voucher(
        "163",
        "03-10",
        "Refusjon programvare",
        "Kjøp",
        [
            _line("71", "2702", "200.00", "03-10"),
            _line("72", "2712", "-200.00", "03-10"),
            _line("73", "239010", "800.00", "03-10"),
            _reverse_charge_line("74", "-800.00", "03-10", "200.00"),
        ],
    )
    content = _file_of([bought, refunded], "2000.00", more=REVERSE_CHARGE_ACCOUNTS)
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert review["postable"] is True, review["findings"]
    assert "flere_mva_koder" not in _codes(review)
    note = next(f for f in review["findings"] if f["code"] == "omvendt_avgift_samlet")
    assert note["message"].startswith("2 av linjene i filen fører omvendt avgiftsplikt (kode 86)")
    assert _post(client, company["id"], content).status_code == 201

    def melding(termin: int) -> tuple[list[tuple[str, str, Any, str]], Any]:
        period = client.get(
            f"/api/v1/companies/{company['id']}/mva/termin?aar={Y}&termin={termin}"
        ).json()
        lines = [
            (ln["mva_kode"], ln["side"], ln["grunnlag"], ln["merverdiavgift"])
            for ln in period["lines"]
        ]
        return lines, Decimal(period["reconciliation"]["difference"])

    # Owed on the purchase and deducted again, as Skatteetaten's example has it,
    # and the VAT accounts agree with the melding.
    assert melding(1) == (
        [("86", "utgaaende", "800.00", "200.00"), ("86", "inngaaende", None, "-200.00")],
        Decimal("0.00"),
    )
    assert melding(2) == (
        [("86", "utgaaende", "-800.00", "-200.00"), ("86", "inngaaende", None, "200.00")],
        Decimal("0.00"),
    )
    verdict = _compare(client, company["id"], content).json()
    assert verdict["matches"] is True, verdict


def _fiken_line(record_id: str, account: str, amount: str, on: str, vat: str) -> str:
    """A sales line with code 3 as Fiken writes all VAT: a debit, on a base of 0."""
    entry = (
        "<TaxInformation><TaxType>MVA</TaxType><TaxCode>3</TaxCode>"
        "<TaxPercentage>25</TaxPercentage><TaxBase>0.00</TaxBase>"
        f"<DebitTaxAmount><Amount>{vat}</Amount></DebitTaxAmount></TaxInformation>"
    )
    return _line(record_id, account, amount, on).replace("</Line>", entry + "</Line>")


def test_a_sale_from_fiken_is_output_vat(client: TestClient) -> None:
    company = _books(client)
    sold = _voucher(
        "31",
        "01-20",
        "Salg, faktura #1001",
        "Salg",
        [
            _line("81", "1500", "1250.00", "01-20", customer="10003"),
            _line("82", "2700", "-250.00", "01-20"),
            _fiken_line("83", "307120", "-1000.00", "01-20", "250.00"),
        ],
    )
    # Half of it credited in the same term.
    credited = _voucher(
        "32",
        "02-10",
        "Kreditnota #20001",
        "Salg",
        [
            _line("91", "1500", "-625.00", "02-10", customer="10003"),
            _line("92", "2700", "125.00", "02-10"),
            _fiken_line("93", "307120", "500.00", "02-10", "125.00"),
        ],
    )
    content = _file_of([sold, credited], "1875.00")
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())

    review = _bilag(client, company["id"], content).json()
    assert review["postable"] is True, review["findings"]
    notes = {f["code"]: f["message"] for f in review["findings"]}
    assert notes["mva_paa_linjens_side"].startswith("1 av linjene i filen har mva-beløpet")
    assert notes["mva_grunnlag_fra_linjen"].startswith("2 av linjene i filen har grunnlag 0")
    assert _post(client, company["id"], content).status_code == 201

    # Read as written, the sale and the kreditnota would both be deductions,
    # with no base: output VAT of -375 where 2700 says 125.
    period = client.get(f"/api/v1/companies/{company['id']}/mva/termin?aar={Y}&termin=1").json()
    assert [
        (ln["mva_kode"], ln["side"], ln["grunnlag"], ln["merverdiavgift"]) for ln in period["lines"]
    ] == [("3", "utgaaende", "500.00", "125.00")]
    assert Decimal(period["reconciliation"]["difference"]) == Decimal("0.00")
    verdict = _compare(client, company["id"], content).json()
    assert verdict["matches"] is True, verdict
