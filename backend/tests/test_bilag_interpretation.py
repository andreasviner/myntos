"""Reading an invoice: the number and date parsing, and the proposals built on it.

Plain strings in, proposals out — no PDF and no database, which is the point of
keeping :mod:`myntos.domain.bilag.interpretation` free of both. The invoice
samples are written the way Norwegian invoices actually come out of a PDF text
layer: label and value on one line, thousands separated by a space, and the due
date sitting one line below the invoice date where a careless reader would grab
the wrong one.
"""

from datetime import date
from decimal import Decimal

import pytest

from myntos.domain.bilag.interpretation import (
    InvoiceSuggestion,
    PartyHint,
    amounts_in,
    dates_in,
    parse_amount,
    read,
    suggest,
)
from myntos.domain.ledger.cashflow import CashFlowDirection

TODAY = date(2026, 8, 13)
OWN_ORGNR = "910000012"
OWN_NAME = "Nordmann Consulting AS"

PURCHASE = """Telenor Norge AS
Snarøyveien 30
1360 Fornebu
Org.nr. 910 000 004 MVA

FAKTURA

Fakturanummer: 104588
Fakturadato: 14.03.2026
Forfallsdato: 28.03.2026

Kunde: Nordmann Consulting AS
Org.nr 910 000 012

Mobilabonnement mars 2026        2   399,00     798,00
Bredbånd 500/500 mars            1   649,00     649,00

Sum eks. mva                                  1 447,00
Mva 25 %                                        361,75
Å betale                                      1 808,75

Kontonr: 1234.56.78901   KID: 000104588000012
"""

OWN_SALE = """Nordmann Consulting AS
Org.nr 910 000 012 MVA

Faktura 2026-014
Fakturadato 02.04.2026
Forfall 16.04.2026

Kunde: Bergen Byggservice AS

Konsulenttimer mars, 20 timer a 1 250,00
Sum eks mva            25.000,00
Merverdiavgift 25 %     6.250,00
Totalt inkl. mva       31.250,00
"""


def only(text: str, *, parties: list[PartyHint] | None = None) -> InvoiceSuggestion:
    """A suggestion for ``text`` on behalf of the test company."""
    return suggest(
        text,
        today=TODAY,
        own_orgnr=OWN_ORGNR,
        own_name=OWN_NAME,
        parties=parties or [],
    )


# --- Amounts --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("1 808,75", "1808.75"),
        ("1.808,75", "1808.75"),
        ("1,808.75", "1808.75"),
        ("1808.75", "1808.75"),
        ("12 500", "12500"),
        # Three digits after the separator make it a thousands separator, and it
        # is the only reading that is right in both conventions.
        ("12.500", "12500"),
        ("12,500", "12500"),
        ("0,25", "0.25"),
    ],
)
def test_a_written_number_reads_the_same_in_both_conventions(written: str, expected: str) -> None:
    assert parse_amount(written) == Decimal(expected)


def test_a_rate_is_not_an_amount() -> None:
    """``Mva 25 % 361,75`` states one amount, not two."""
    assert amounts_in("Mva 25 %  361,75") == [Decimal("361.75")]


def test_a_date_is_not_an_amount() -> None:
    """Otherwise every invoice would appear to have been paid 14 kroner and 3 øre."""
    assert amounts_in("Fakturadato: 14.03.2026") == []


def test_an_account_number_is_not_an_amount() -> None:
    assert amounts_in("Kontonr: 1234.56.78901") == []


# --- Dates ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    ["14.03.2026", "14/03/2026", "2026-03-14", "14. mars 2026", "14 mar 2026"],
)
def test_the_usual_date_formats_are_read(written: str) -> None:
    assert dates_in(f"Fakturadato {written}", today=TODAY) == [date(2026, 3, 14)]


def test_an_implausible_year_is_not_a_date() -> None:
    """A reference number that happens to look like a date is not one."""
    assert dates_in("Ref 12.03.1904", today=TODAY) == []


# --- Reading a purchase invoice ---------------------------------------------------


def test_a_supplier_invoice_is_read_off_the_page() -> None:
    found = read(PURCHASE, today=TODAY)

    assert found.gross_amount == Decimal("1808.75")
    assert found.net_amount == Decimal("1447.00")
    assert found.vat_amount == Decimal("361.75")
    assert found.vat_rate == Decimal("0.25")
    assert found.invoice_number == "104588"
    assert found.seller_name == "Telenor Norge AS"
    assert found.currency_code is None


def test_the_due_date_never_becomes_the_posting_date() -> None:
    found = read(PURCHASE, today=TODAY)
    assert found.invoice_date == date(2026, 3, 14)
    assert found.due_date == date(2026, 3, 28)


def test_both_organisasjonsnummer_are_found_in_the_order_printed() -> None:
    """The sender's first: that is what makes it the counterparty's."""
    assert read(PURCHASE, today=TODAY).orgnrs == ("910000004", "910000012")


def test_a_purchase_becomes_money_out_with_a_type_and_a_vat_code() -> None:
    suggestion = only(PURCHASE)

    assert suggestion.direction is CashFlowDirection.UT
    assert suggestion.type_code == "telefon_internett"
    assert suggestion.tax_code == "1"
    assert suggestion.occurred_on == date(2026, 3, 14)
    assert suggestion.gross_amount == Decimal("1808.75")
    assert suggestion.description == "Faktura 104588, Telenor Norge AS"
    assert suggestion.counterparty_orgnr == "910000004"
    # Everything proposed is accounted for, and nothing else claims to be.
    assert set(suggestion.filled) == {
        "occurred_on",
        "gross_amount",
        "type_code",
        "tax_code",
        "description",
    }
    assert suggestion.warnings == []


def test_a_registered_supplier_is_recognised_by_organisasjonsnummer() -> None:
    supplier = PartyHint(id=42, name="Telenor Norge AS", orgnr="910000004", is_customer=False)
    suggestion = only(PURCHASE, parties=[supplier])

    assert suggestion.party_id == 42
    assert suggestion.direction is CashFlowDirection.UT
    assert "party_id" in suggestion.filled


# --- Reading an invoice we issued ourselves ---------------------------------------


def test_an_invoice_in_our_own_name_becomes_money_in() -> None:
    suggestion = only(OWN_SALE)

    assert suggestion.direction is CashFlowDirection.INN
    # A sale is categorised by its rate, and 25 % has exactly one home.
    assert suggestion.type_code == "salg_hoy_sats"
    assert suggestion.tax_code == "3"
    assert suggestion.gross_amount == Decimal("31250.00")
    assert suggestion.occurred_on == date(2026, 4, 2)


def test_the_counterparty_on_our_own_invoice_is_the_customer() -> None:
    """Not the sender — the sender is us, which is no help to anybody."""
    customer = PartyHint(id=7, name="Bergen Byggservice AS", orgnr=None, is_customer=True)
    suggestion = only(OWN_SALE, parties=[customer])

    assert suggestion.party_id == 7
    assert suggestion.counterparty_name == "Bergen Byggservice AS"
    assert suggestion.description == "Faktura 2026-014, Bergen Byggservice AS"


# --- Where a guess would be worse than a question ---------------------------------


def test_a_scanned_pdf_proposes_nothing_and_says_why() -> None:
    suggestion = only("")

    assert suggestion.gross_amount is None
    assert suggestion.occurred_on is None
    assert suggestion.filled == []
    assert "skannet" in suggestion.warnings[0]


def test_an_unrecognisable_purchase_leaves_the_type_alone() -> None:
    """Rather than reaching for "annen driftskostnad" to have an answer."""
    suggestion = only("Faktura 12\nFakturadato 01.02.2026\nÅ betale 500,00\n")

    assert suggestion.gross_amount == Decimal("500.00")
    assert suggestion.type_code is None
    assert any("Velg type selv" in warning for warning in suggestion.warnings)


def test_vat_on_a_car_expense_is_not_silently_deducted() -> None:
    """The rate is printed, the deduction is still the user's call.

    A passenger car gives no input VAT deduction however much VAT the seller
    charged, so the type's assumption stands and the document's rate becomes a
    sentence rather than a code.
    """
    suggestion = only("Circle K Storo\n04.06.2026\nDiesel 41,2 liter\nTotalt 812,00\nMva 162,40\n")

    assert suggestion.type_code == "bilkostnad"
    assert suggestion.tax_code is None
    assert any("uten fradrag" in warning for warning in suggestion.warnings)


def test_an_invoice_without_vat_asks_about_reverse_charge() -> None:
    text = (
        "Acme Cloud Inc\nInvoice number INV-9931\nInvoice date 2026-05-04\n"
        "Software license, subscription\nSubtotal 100.00\nVAT (0%) 0.00\nTotal due 100.00\n"
    )
    suggestion = only(text)

    assert suggestion.type_code == "datakostnad"
    assert suggestion.tax_code is None
    assert any("omvendt avgiftsplikt" in warning for warning in suggestion.warnings)


def test_a_foreign_currency_is_pointed_out() -> None:
    text = "Acme Cloud Inc\nInvoice date 2026-05-04\nTotal due EUR 100.00\n"
    suggestion = only(text)

    assert suggestion.currency_code == "EUR"
    assert any("EUR" in warning for warning in suggestion.warnings)


def test_kroner_are_not_mistaken_for_a_foreign_currency() -> None:
    """ "Euro" in a footer does not make an invoice in kroner a foreign one."""
    text = "Faktura 5\nFakturadato 01.03.2026\nÅ betale NOK 500,00\nEuropris AS\n"
    assert only(text).currency_code is None


def test_a_date_in_the_future_is_flagged_rather_than_trusted() -> None:
    text = f"Faktura 9\nFakturadato 02.01.{TODAY.year + 1}\nÅ betale 100,00\n"
    suggestion = only(text)

    assert suggestion.occurred_on == date(TODAY.year + 1, 1, 2)
    assert any("fram i tid" in warning for warning in suggestion.warnings)


def test_a_credit_note_says_that_it_is_one() -> None:
    text = "KREDITNOTA\nKreditnotanr 88\nFakturadato 03.03.2026\nÅ betale 250,00\n"
    suggestion = only(text)

    assert suggestion.description is not None
    assert suggestion.description.startswith("Kreditnota 88")
    assert any("kreditnota" in warning.lower() for warning in suggestion.warnings)
