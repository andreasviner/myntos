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

from myntos.domain.bilag import interpretation
from myntos.domain.bilag.interpretation import (
    FieldSource,
    InvoiceSuggestion,
    PartyHint,
    _clean_name,
    amounts_in,
    apply_conversion,
    apply_type,
    dates_in,
    note_capitalisation,
    parse_amount,
    read,
    suggest,
)
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES, CashFlowDirection

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
    # party_id is among them although no party matched: the sender is named on
    # the invoice, and the form offers that name as a motpart to register.
    assert set(suggestion.filled) == {
        "occurred_on",
        "gross_amount",
        "type_code",
        "tax_code",
        "description",
        "party_id",
    }
    assert suggestion.warnings == []


def test_a_registered_supplier_is_recognised_by_organisasjonsnummer() -> None:
    supplier = PartyHint(id=42, name="Telenor Norge AS", orgnr="910000004", is_customer=False)
    suggestion = only(PURCHASE, parties=[supplier])

    assert suggestion.party_id == 42
    assert suggestion.direction is CashFlowDirection.UT
    assert "party_id" in suggestion.filled


def test_a_sender_nobody_registered_is_still_offered_as_a_motpart() -> None:
    """There is no party row to point at, but the invoice names one.

    The form puts that name in the motpart list as its own choice, and registers
    it under the name if the user posts with it selected. So the field counts as
    proposed, marked with where the name came from rather than where a party row
    came from.
    """
    suggestion = only(PURCHASE)

    assert suggestion.party_id is None
    assert suggestion.counterparty_name == "Telenor Norge AS"
    assert suggestion.sources["party_id"] is FieldSource.DOCUMENT


def test_app_chrome_is_not_a_counterparty() -> None:
    """Four phone screenshots, and the top of each is a toolbar.

    The last-resort rule takes the first name-shaped line off the top of the
    page, which is a letterhead on an invoice and a status bar on a screenshot.
    A bank app's "Add receipt" button and an AliExpress search box both reached
    the motpart field of a purchase this way. The list that already refused
    these words was applied to the model's answers and not to the parser's, and
    the model is asked for a name only where the parser found none.
    """
    text = (
        "16:04\nReserved 05. September 2026\n- NOK 1 011,44\n"
        "Add receipt\nUpload\nDetails\nAmount\nNOK -1 011,44\n"
        "Account\nLINDEMAN TECHNOLOGIES AS\nShow more\nHome\nPayments\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7))

    assert reading.seller_name is None


def test_a_line_the_phone_cut_short_is_not_a_counterparty() -> None:
    """ "Order ID, product or sto..." is a search box with no room for its label."""
    assert interpretation._clean_name("Order ID, product or sto...") is None
    assert interpretation._clean_name("Eite Technology Store") == "Eite Technology Store"


# --- A bank statement states its direction, an invoice does not ------------------

REFUND = (
    "Details\nAmount\nNOK 26 266,40\nAccount\nLINDEMAN TECHNOLOGIES AS\n1520.34.91244\n"
    "Booked\n03.09.2026\nDate\n23.08.2026\nMerchant name\nEbay O:10-15068-4\n"
    "\n--- Dokument 2: IMG_9367.png ---\n"
    "Details\nAmount\nNOK -27 850,81\nAccount\nLINDEMAN TECHNOLOGIES AS\n1520.34.91244\n"
    "Booked\n24.08.2026\nDate\n23.08.2026\nMerchant name\nEbay O:10-15068-4\n"
)


def test_a_minus_on_a_bank_line_means_the_money_left() -> None:
    """The one thing a statement says about direction and an invoice never does.

    An invoice for 12 500 and a card purchase of 12 500 are the same figure. Our
    amount pattern had no sign group at all, so "NOK -27 850,81" and "NOK
    26 266,40" arrived as two positive numbers with nothing to tell them apart.
    """
    reading = interpretation.read(REFUND, today=date(2026, 9, 7), has_letterhead=False)

    # 26 266,40 back against 27 850,81 out: the company is worse off by the
    # difference, which is the exchange rate moving between the two.
    assert reading.gross_amount == Decimal("1584.41")
    assert reading.total_is_negative is True
    assert reading.shows_a_return is True


def test_a_bilag_is_the_sum_of_what_is_on_it() -> None:
    """Not the largest of the figures, and not the first: all of them.

    Nine screenshots of an AliExpress order that charged the card three times
    are one voucher of 1 758,29. Largest and first were arbitrary in the same
    way, answering "which of these is the bilag" when the answer is "all".
    """
    text = (
        "Details\nAmount\nNOK -146,23\n1520.34.91244\n"
        "\n--- Dokument 2: b.png ---\nDetails\nAmount\nNOK -1 011,44\n1520.34.91244\n"
        "\n--- Dokument 3: c.png ---\nDetails\nAmount\nNOK -600,62\n1520.34.91244\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("1758.29")
    assert reading.total_is_negative is True
    assert reading.shows_a_return is False


def test_the_same_payment_from_two_sides_is_counted_once() -> None:
    """The bank writes the charge as -600,62 and the shop as 600,62.

    Counted twice it doubles the bilag; matched by sign as well as by figure it
    cancels to nothing. It is one payment, and the bank is the one saying which
    way it went.
    """
    text = (
        "Details\nAmount\nNOK -600,62\n1520.34.91244\n"
        "\n--- Dokument 2: order.png ---\nTotal for 1 items: 600,62 kr\n1520.34.91244\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("600.62")
    assert reading.total_is_negative is True
    assert reading.shows_a_return is False


def test_a_receipts_fee_line_is_not_added_to_the_charge_that_includes_it() -> None:
    """The FINN receipt says 3 250 plus 315 in fees; the account was charged 3 565.

    Adding every document's total gave 3 880, which is the charge plus a part of
    itself. When a file on the bilag prints a kontonummer, that is the file that
    says what moved, and the rest describe the same money in more detail.
    """
    text = (
        "Kjøpesum 3 250,00\nAvgift for Trygg betaling 206,00\nFrakt 109,00\nTotalt 315,00\n"
        "\n--- Dokument 2: bank.png ---\n"
        "Details\nAmount\nNOK -3 565,00\nAccount\nLINDEMAN TECHNOLOGIES AS\n1520.34.91244\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("3565.00")


def test_with_nothing_saying_what_moved_the_largest_total_is_the_bilag() -> None:
    """Adding up needs amounts that are independent of each other.

    With no file saying what left the account, nothing on the bilag is
    independent: the files are one payment written at different granularities.
    The FINN bilag prints "Totalt 3 565 kr" on the purchase and "Totalt 315,00
    kr" on the fee receipt, and the 315 is inside the 3 565.
    """
    text = "Totalt 3 565,00\n\n--- Dokument 2: gebyr.png ---\nTotalt 315,00\n"
    reading = interpretation.read(text, today=date(2026, 9, 7))

    assert reading.gross_amount == Decimal("3565.00")


def test_one_purchase_documented_twice_is_not_two_purchases() -> None:
    """The FINN advert and the bank line both say 7 500,00."""
    text = (
        "Totalt 7 500,00\n1520.34.91244\n"
        "\n--- Dokument 2: bank.png ---\nAmount\nNOK 7 500,00\n1520.34.91244\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("7500.00")


def test_a_bank_prints_the_same_payment_twice_on_one_screen() -> None:
    """Once large at the top and once in a details table, and it is one payment.

    Within one document the largest labelled figure is still the total, which is
    what keeps an invoice's subtotals from being added to it.
    """
    text = (
        "Reserved 05. September 2026\n- NOK 146,23\nDetails\nAmount\nNOK -146,23\n1520.34.91244\n"
    )
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("146.23")


def test_an_invoice_is_unchanged_because_it_signs_nothing() -> None:
    """The blast radius of the sign rule is documents that print a sign."""
    reading = interpretation.read(PURCHASE, today=date(2026, 9, 7))

    assert reading.total_is_negative is None
    assert reading.shows_a_return is False
    assert reading.gross_amount == Decimal("1808.75")


def test_a_net_of_two_legs_says_that_it_is_one() -> None:
    """A difference does not look like a difference in an amount field.

    27 850,81 out and 26 266,40 back for the same GBP 2 137,00 leaves 1 584,41,
    which is the exchange rate moving between the two and not something that was
    bought. Unexplained it reads as a small purchase.
    """
    reading = interpretation.read(REFUND, today=date(2026, 9, 7), has_letterhead=False)
    suggestion = interpretation.suggest(
        REFUND,
        today=date(2026, 9, 7),
        own_orgnr="938193045",
        own_name="Lindeman Technologies AS",
        reading=reading,
    )

    assert suggestion.gross_amount == Decimal("1584.41")
    assert any("differansen mellom dem" in warning for warning in suggestion.warnings)
    assert any("valutatap" in warning for warning in suggestion.warnings)


def test_money_that_came_back_is_not_told_to_charge_vat_on_it() -> None:
    """A return filed as a sale adds output VAT the company does not owe.

    25 % of the 26 266,40 eBay sent back is 5 253 kroner of tax on a purchase
    that was cancelled. Said rather than overruled: the catalogue has no home
    for a refund, since every money-in category in it is a revenue one.
    """
    reading = interpretation.read(REFUND, today=date(2026, 9, 7), has_letterhead=False)
    suggestion = interpretation.suggest(
        REFUND,
        today=date(2026, 9, 7),
        own_orgnr="938193045",
        own_name="Lindeman Technologies AS",
        reading=reading,
    )
    interpretation.apply_type(
        suggestion,
        reading=reading,
        type_code="salg_hoy_sats",
        source=interpretation.FieldSource.MODEL,
    )

    assert suggestion.tax_code is None
    assert any("refusjon og ikke et salg" in warning for warning in suggestion.warnings)


def test_an_ordinary_sale_is_not_accused_of_being_a_refund() -> None:
    """One sign on the page is one direction, and nothing to warn about."""
    text = "Details\nAmount\nNOK 12 500,00\nAccount\nLINDEMAN TECHNOLOGIES AS\n"
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)
    suggestion = interpretation.suggest(
        text,
        today=date(2026, 9, 7),
        own_orgnr="938193045",
        own_name="Lindeman Technologies AS",
        reading=reading,
    )
    interpretation.apply_type(
        suggestion,
        reading=reading,
        type_code="salg_hoy_sats",
        source=interpretation.FieldSource.MODEL,
    )

    assert reading.shows_a_return is False
    assert not any("refusjon" in warning for warning in suggestion.warnings)


def test_a_label_is_a_label_and_not_a_prefix() -> None:
    """ "Selgeren har sendt pakken" is a status line, not "Selger" plus a name.

    Without a word boundary the label was stripped off the front of the word it
    is part of, and a FINN status page arrived with the counterparty "en har
    sendt pakken".
    """
    assert interpretation._clean_name("Selger: Anders A") == "Anders A"
    assert interpretation._clean_name("Fra Telenor Norge AS") == "Telenor Norge AS"
    assert interpretation._clean_name("Selgeren har sendt pakken") == "Selgeren har sendt pakken"


def test_a_clause_never_becomes_the_sellers_name() -> None:
    """A name that is a sentence does more damage than fill one field.

    It becomes the bilagstekst when the item line is missing, and it is what a
    registered party is matched against. "Forsikre produktet mot uhell" is a
    checkbox on FINN sitting one line under "Selger", which is a label and
    normally tells the truth.
    """
    text = "Selger\nForsikre produktet mot uhell\nTotalt 3 000 kr\n"
    reading = interpretation.read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.seller_name is None


def test_a_camera_roll_title_is_not_a_counterparty() -> None:
    """The title is a last-resort sender, and a photograph has one too.

    An eBay order confirmation says "eBay" in its title and nowhere in its text,
    which is why the fallback exists. A screenshot's title is "IMG_9362", and
    that reached the motpart field once the chrome above it was refused.
    """
    assert interpretation._clean_title("IMG_9362.png") is None
    assert interpretation._clean_title("Screenshot 2026-09-05 at 16.04.png") is None
    assert interpretation._clean_title("eBay") == "eBay"


def test_a_clause_off_a_status_page_is_not_offered_as_a_motpart() -> None:
    """Shown in the bilagstekst is one thing, registered as a party another.

    "en har sendt pakken" is the tail of "Selgeren har sendt pakken" on a FINN
    status page. It is genuinely printed there, so it can end up in
    ``counterparty_name``, but a party register full of sentences is worse than
    one that is short.
    """
    assert not interpretation.offerable("en har sendt pakken")
    assert interpretation.offerable("Linjeforeningen Elektra")
    # A lone lowercase token is a marketplace handle, and handles are most of
    # the counterparties this system meets.
    assert interpretation.offerable("daisyaus-0")


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
    # Code 0, not the category's default of 25 %. Neither is certainly right on
    # a foreign invoice, but only one of them claims a deduction of 20 kroner
    # that the seller never charged.
    assert suggestion.tax_code == "0"
    assert any("omvendt avgiftsplikt" in warning for warning in suggestion.warnings)


def test_a_zero_in_the_mva_column_beats_the_categorys_default_rate() -> None:
    """Brønnøysundregistrene's registreringsgebyr, as its text layer comes out.

    The rate is not on a line that says "mva". It is the last column of the item
    row, welded to the amount before it by the extractor, under a heading that
    names the column. Read only for labelled lines, the invoice says nothing
    about VAT, and the form fell back to 25 % and proposed a deduction of 1 365
    kroner on a public fee that carries none.
    """
    text = (
        "NOKARTIKKEL ENH ANTALL PRIS BELØP IMVA\n"
        "0411011 Aksjeselskap STK 1,0 6 825,00 6 825,000%\n"
        "Fakturanummer: 935091521\n"
        "Fakturadato : 01.09.2026\n"
        "Organisasjonsnummer: 938 386 730\n"
        "SUM I NOK : 6 825,00\n"
    )
    suggestion = only(text)

    assert suggestion.gross_amount == Decimal("6825.00")
    assert suggestion.tax_code == "0"
    assert suggestion.sources["tax_code"] is FieldSource.DOCUMENT
    assert suggestion.vat_amount == Decimal(0)


def test_a_lone_mva_label_does_not_turn_discounts_into_vat_rates() -> None:
    """The column rule needs a column, which means a heading that names several.

    A "Mva" over a total is a label. Treating it as a table heading would make
    the 10 % in a discount column the VAT rate of the whole bilag.
    """
    text = "Faktura 77\nFakturadato 01.03.2026\nSkruer 4 stk 500,00 10%\nMva\nÅ betale 500,00\n"
    suggestion = only(text)

    assert suggestion.tax_code is None


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


# --- Documents that are not Norwegian invoices ------------------------------------

# What a webshop receipt looks like once pypdf has flattened it: section headings
# where an invoice has a sender, the date written month first, the total in
# pounds, and not one of the words our labels look for.
RECEIPT = """Order information
Buyer andreali_7
Seller daisyaus-0
Placed on Aug 23, 2026
Payment method Credit Card
Shipping address
Andreas Lindeman
Magnus den Godes gate 8
Trondheim 7030
Norway
Order total
1 item GBP 2,137.00
Shipping Free
Order total GBP 2,137.00
Items bought from daisyaus-0
Order number: 10-15068-44333
Quantity Item name Shipping service Item price
1
NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU
(800545236002)
Economy Int'l Postage
"""


def test_a_date_written_month_first_is_still_a_date() -> None:
    assert dates_in("Placed on Aug 23, 2026", today=TODAY) == [date(2026, 8, 23)]
    assert dates_in("Order date September 3rd 2026", today=TODAY) == [date(2026, 9, 3)]


def test_a_month_and_a_year_without_a_day_are_not_a_date() -> None:
    """ "Mobilabonnement mars 2026" is a line on an invoice, not its date."""
    assert dates_in("Mobilabonnement mars 2026        2   399,00", today=TODAY) == []


def test_a_receipt_is_read_even_though_it_says_none_of_the_words() -> None:
    found = read(RECEIPT, today=TODAY)

    assert found.invoice_date == date(2026, 8, 23)
    assert found.gross_amount == Decimal("2137.00")
    # "Items bought from" names the seller where an invoice would print an
    # organisasjonsnummer, and "Order information" is a heading, not a company.
    assert found.seller_name == "daisyaus-0"
    assert found.invoice_number == "10-15068-44333"
    assert found.number_kind == "Ordre"
    assert found.currency_code == "GBP"


def test_a_receipt_is_described_as_the_order_it_is() -> None:
    """The reference the auditor follows, then what the money was actually for."""
    assert only(RECEIPT).description == (
        "Ordre 10-15068-44333, NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU"
    )


def test_a_total_in_a_foreign_currency_is_shown_but_never_proposed() -> None:
    """2 137 in a kroner field is wrong by the exchange rate, and looks right."""
    suggestion = only(RECEIPT)

    assert suggestion.gross_amount is None
    assert suggestion.foreign_amount == Decimal("2137.00")
    assert "gross_amount" not in suggestion.filled
    assert any("2\u00a0137,00 GBP" in warning for warning in suggestion.warnings)


def test_foreign_vat_is_never_proposed_as_norwegian_input_vat() -> None:
    """Whatever the seller charged in euro, no code here makes it deductible."""
    text = (
        "Acme Cloud Inc\nInvoice date 2026-05-04\nSoftware license\n"
        "Subtotal EUR 100.00\nVAT 25 % EUR 25.00\nTotal due EUR 125.00\n"
    )
    suggestion = only(text)

    assert suggestion.type_code == "datakostnad"
    # Proposed as 0 rather than left empty: the form fills an empty code with
    # the category's default, and that is 25 % for "datakostnad".
    assert suggestion.tax_code == "0"
    assert any("innførselsmva" in warning for warning in suggestion.warnings)


def test_goods_from_abroad_are_not_given_the_categorys_25_percent() -> None:
    """MA-3D's pro forma invoice: 1 790 EUR, "VAT 0,00 €", import VAT to follow.

    Left without a code it came back as "1: Inngående mva, høy sats (25 %)",
    the default for Varekjøp, which deducts a fifth of the amount as VAT the
    seller never charged. The import VAT is a separate bill from the freight
    forwarder, and the warning says so.
    """
    text = (
        "FjordLab Design, d.o.o.\nDate of issue: 03.10.2026\nJet Thruster JetX-100V4\n"
        "Subtotal 1.790,00 €\nVAT 0,00 €\nTotal 1.790,00 €\n"
    )
    found = read(text, today=TODAY)
    proposal = suggest(text, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME, reading=found)
    apply_type(proposal, reading=found, type_code="varekjop", source=FieldSource.MODEL)

    assert proposal.tax_code == "0"
    assert any("tollpass" in warning for warning in proposal.warnings)


def test_the_tollpass_bill_in_foreign_currency_keeps_its_own_code() -> None:
    """Filed as the import VAT itself, the bilag is that bill, and 14 is right."""
    text = "Customs clearance\nDate 10.10.2026\nImport VAT EUR 447,50\nTotal EUR 447,50\n"
    found = read(text, today=TODAY)
    proposal = suggest(text, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME, reading=found)
    apply_type(proposal, reading=found, type_code="mva_innforsel", source=FieldSource.MODEL)

    assert proposal.tax_code is None


def test_a_field_says_where_it_came_from() -> None:
    suggestion = only(PURCHASE)

    assert suggestion.sources["occurred_on"] is FieldSource.DOCUMENT
    assert suggestion.type_source is FieldSource.DOCUMENT
    assert suggestion.filled == list(suggestion.sources)


# --- What was bought, the file's own title, and the kroner it cost ----------------


def test_the_description_says_what_was_bought_rather_than_who_sold_it() -> None:
    """The complaint that started this: "Ordre 10-15068-44333, daisyaus-0".

    The seller's name has a field of its own a few centimetres away, so spending
    the description on it says the same thing twice and leaves the one question
    the description exists to answer unanswered. The item table says what was
    bought, and no model is needed to read it.
    """
    proposal = suggest(RECEIPT, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)

    assert proposal.description == (
        "Ordre 10-15068-44333, NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU"
    )
    # Read, not guessed: the line is printed under the item heading.
    assert proposal.sources["description"] is FieldSource.DOCUMENT
    # And the counterparty still has its own field.
    assert proposal.counterparty_name == "daisyaus-0"


def test_a_totals_row_under_a_heading_is_not_an_item() -> None:
    """ "1 item GBP 2,137.00" holds the word "item" and is not a heading.

    The check that tells them apart is that a heading names columns and holds no
    figures. Without it the reader anchors on the summary line four rows above
    the real table and proposes "Order total GBP 2,137.00" as the description,
    which is worse than proposing nothing.
    """
    found = read(RECEIPT, today=TODAY)

    assert found.item_text == "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU"


def test_an_invoice_without_an_item_table_still_names_the_counterparty() -> None:
    """No heading, no item text, and the description is what it always was."""
    found = read(PURCHASE, today=TODAY)
    proposal = suggest(PURCHASE, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)

    assert found.item_text is None
    assert proposal.description == "Faktura 104588, Telenor Norge AS"


def test_the_file_title_names_the_seller_when_the_page_does_not() -> None:
    """A webshop receipt often says who it is from only in its own title.

    "Order details | eBay" is the single occurrence of the word eBay in that
    document once the logo turns out to be an image, so the title is the whole
    of what is knowable about the shop. The trailing furniture is trimmed and
    the naming half is kept.
    """
    found = read("Kvittering\nTotalt 500,00 kr", today=TODAY, title="Order details | eBay")

    assert found.document_title == "eBay"
    assert found.seller_name == "eBay"
    # And says so, because a title is also where a filename ends up, and the
    # model gets to replace a name that came from nowhere better.
    assert found.seller_named_by_title


def test_the_title_never_outranks_a_name_on_the_page() -> None:
    """It is a fallback. A document that names its sender has already answered."""
    found = read(PURCHASE, today=TODAY, title="Order details | eBay")

    assert found.document_title == "eBay"
    assert found.seller_name == "Telenor Norge AS"
    assert not found.seller_named_by_title


# The layout of MA-3D's pro forma invoice as Løynd transcribes it, with the
# names changed: the bank-details block first, the buyer's address under it,
# and the letterhead at the very bottom, past where the top-of-page guess looks.
PRO_FORMA = """[Page 1]
Payment details:
Beneficiary´s Name: FJORDLAB DESIGN D.O.O.
Beneficiary´s address: CESTA 84, 1211 LJUBLJANA, SLOVENIA
Bank Name: OTP BANKA D.D.
SWIFT code: KBMASI2X
Bank account: SI56 0400 0027 9713 506

Kari Nordmann
7030 Trondheim
Norway
Description Price Qty Unit VAT Amount
Jet Thruster JetX-100V4
535,00 € 1 piece 535,00 €
Subtotal 1.790,00 €
VAT 0,00 €
Total 1.790,00 €
FjordLab Design, d.o.o.
VAT ID: SI49294890
Pro Forma Invoice No.: 26-0023
Date of issue: Ljubljana, 03.10.2026
"""  # noqa: RUF001 - the acute accent is what the invoice prints


def test_the_beneficiary_of_the_payment_is_the_seller() -> None:
    """Who the money goes to is a statement of who sold it, like "Sold by".

    The file was saved under the buyer's name, so before this the title
    fallback offered the buyer as the counterparty of their own purchase.
    """
    found = read(PRO_FORMA, today=TODAY, title="PI Kari Nordmann _26-0023")

    assert found.seller_name == "FJORDLAB DESIGN D.O.O"
    assert not found.seller_named_by_title


def test_a_foreign_total_becomes_kroner_once_a_rate_is_supplied() -> None:
    """The field that used to be left empty, filled and shown its arithmetic.

    The rate is the caller's to fetch (myntos.integrations.norgesbank); what is
    tested here is what the domain does with it. The amount is marked COMPUTED
    rather than DOCUMENT because the document does not say it, and the sentence
    carries the rate at the four decimals Norges Bank quotes so that the
    multiplication can actually be repeated.
    """
    proposal = suggest(RECEIPT, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    assert proposal.gross_amount is None
    assert proposal.foreign_amount == Decimal("2137.00")
    assert any("står tomt med vilje" in warning for warning in proposal.warnings)

    apply_conversion(
        proposal,
        kroner=Decimal("27108.49"),
        rate=Decimal("12.6853"),
        quoted_on=date(2026, 8, 21),
    )

    assert proposal.gross_amount == Decimal("27108.49")
    assert proposal.sources["gross_amount"] is FieldSource.COMPUTED
    assert proposal.exchange_rate == Decimal("12.6853")
    assert proposal.rate_quoted_on == date(2026, 8, 21)
    # The old sentence is gone rather than sitting under the filled-in field
    # telling the user it was left empty on purpose.
    assert not any("står tomt med vilje" in warning for warning in proposal.warnings)
    assert any("12,6853" in warning and "21.08.2026" in warning for warning in proposal.warnings)


def test_a_large_purchase_is_flagged_as_a_possible_driftsmiddel() -> None:
    """Under the threshold in pounds, over it in kroner.

    Which is exactly why the test is re-run after the conversion rather than
    once at the end of `suggest`: 2 137 is a small number and 27 108 is not, and
    only one of them is the number skatteloven § 14-40 is about.
    """
    proposal = suggest(RECEIPT, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    assert not any("driftsmiddel" in warning for warning in proposal.warnings)

    apply_conversion(
        proposal,
        kroner=Decimal("27108.49"),
        rate=Decimal("12.6853"),
        quoted_on=date(2026, 8, 21),
    )

    assert sum("driftsmiddel" in warning for warning in proposal.warnings) == 1


def test_the_driftsmiddel_note_is_not_repeated_when_the_category_changes() -> None:
    """It depends on the category, so it is redone; redone is not the same as added."""
    proposal = suggest(RECEIPT, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    apply_conversion(
        proposal,
        kroner=Decimal("27108.49"),
        rate=Decimal("12.6853"),
        quoted_on=date(2026, 8, 21),
    )
    found = read(RECEIPT, today=TODAY)

    apply_type(proposal, reading=found, type_code="inventar", source=FieldSource.MODEL)
    note_capitalisation(proposal)

    assert sum("driftsmiddel" in warning for warning in proposal.warnings) == 1


def test_a_category_that_is_never_a_driftsmiddel_gets_no_note() -> None:
    """Goods for resale are large by nature and are not fixed assets."""
    proposal = suggest(RECEIPT, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    apply_conversion(
        proposal,
        kroner=Decimal("27108.49"),
        rate=Decimal("12.6853"),
        quoted_on=date(2026, 8, 21),
    )
    found = read(RECEIPT, today=TODAY)

    apply_type(proposal, reading=found, type_code="varekjop", source=FieldSource.DOCUMENT)
    note_capitalisation(proposal)

    assert not any("driftsmiddel" in warning for warning in proposal.warnings)


# --- What the fixture bilag in regnaskaps_tests/ taught us --------------------
#
# Every test below stands for a document that came out wrong. They are written
# from the text those documents actually produce, because the failures were all
# in the shape of the text rather than in the arithmetic on it.

# Brønnøysundregistrene's own faktura, as pypdf flattens it. The *customer's*
# organisasjonsnummer is printed near the top under a heading and the sender's
# only in a footer — which is the whole difficulty.
BRREG = """0411011 Aksjeselskap STK 1,0 6 825,00 6 825,000%
Deres ref:
Vår ref: 2026-2478488
Organisasjonsnummer: 938 386 730
Foretaksnavn:DIALGA AS
SUM I NOK : 6 825,00
Brønnøysundregistrene, Pb 900, 8910 BRØNNØYSUND  Telefon: 75 00 75 00 firmapost@brreg.no
 Bank: Nordea Bank, SWIFT: NDEANOKK, IBAN: NO59 63450530699, Organisasjonsnr.: NO 974760673
BESKRIVELSE
F A K T U R A
Fakturanummer: 935091521
Fakturadato : 01.09.2026
"""

CUSTOMER_ORGNR = "938386730"


def test_being_named_first_is_not_proof_we_sent_it() -> None:
    """The worst outcome in the whole pipeline, and it was reachable.

    Named first used to be enough to read a document as one we issued, and this
    invoice names its recipient first. A 6 825-krone registration fee therefore
    came back as money *in* — and because the category menu is narrowed by the
    direction, the only answers on offer were kinds of sale.
    """
    proposal = suggest(BRREG, today=TODAY, own_orgnr=CUSTOMER_ORGNR, own_name="Dialga AS")
    assert proposal.direction.value == "ut"
    assert proposal.gross_amount == Decimal("6825.00")


def test_the_sender_is_looked_for_beside_the_other_partys_number() -> None:
    """Not beside the first number on the page, which may well be ours."""
    found = read(BRREG, today=TODAY, own_orgnr=CUSTOMER_ORGNR)
    # Our own reference block is not the sender's name.
    assert found.seller_name != "Deres ref"
    assert found.seller_name is None or "938 386 730" not in found.seller_name


def test_a_field_label_is_not_a_counterparty() -> None:
    """What is left of "Org.nr: NO 991 107 711 MVA" once the number is taken off.

    None rather than the label, so the model's MOTPART can fill the gap. An
    empty counterparty is a question; "Org.nr" is a wrong answer wearing the
    clothes of a read one.
    """
    for label in ("Org.nr", "Deres ref", "Vår ref", "nummer", "Levering", "Status"):
        assert _clean_name(label) is None
    assert _clean_name("Linjeforeningen Elektra") == "Linjeforeningen Elektra"


def test_a_bank_footer_is_not_a_counterparty() -> None:
    """It sits where the sender's organisasjonsnummer sits, and reads as prose."""
    assert _clean_name("Bank: Nordea Bank, SWIFT: NDEANOKK, IBAN: NO59 63450530699") is None


def test_a_sentence_is_not_a_counterparty() -> None:
    """Off a FINN status page, and genuinely printed there — which is the trap.

    The quote check can say the words are on the page. It cannot say they are
    a party's name, and on a page of prose that is the entire difficulty.
    """
    assert _clean_name("en valgte deg. Vi oppdaterer informasjonen her når") is None


def test_an_extractor_section_label_is_not_a_counterparty() -> None:
    """ "[Page 1]" is Løynd's heading, at the top of the text where a name sits."""
    assert _clean_name("[Page 1]") is None


def test_a_letterspaced_heading_is_not_what_the_payment_was_for() -> None:
    """ "F A K T U R A" is the word FAKTURA, and it reached the ledger as prose."""
    found = read(BRREG, today=TODAY, own_orgnr=CUSTOMER_ORGNR)
    assert found.item_text != "F A K T U R A"


# Linjeforeningen Elektra's invoice: the total is labelled by its payment
# instruction and the rate only by a two-row column heading.
ELEKTRA = """LINJEFORENINGEN ELEKTRA
Org.nr.: NO 991 107 711 MVA
FAKTURA
Fakturanr.: 1075
Fakturadato: 2026-09-06
Ordrenummer: 79 Avdeling: 04 Verksted
Beskrivelse Antall Enh.pris
Beløp
Mva
(25 %)
Beløp
(inkl. mva)
Batteri 1 1 152,00 1 152,00 288,00 1 440,00
Betales til bankkonto 4202.11.39496 NOK  1 440,00
"""


def test_a_payment_instruction_states_the_total() -> None:
    """ "Betales til bankkonto 4202.11.39496 NOK 1 440,00" — the only total line.

    The last figure on the line is the amount, so the kontonummer in front of it
    is not mistaken for one.
    """
    assert read(ELEKTRA, today=TODAY).gross_amount == Decimal("1440.00")


def test_an_abbreviated_label_still_names_the_invoice() -> None:
    """ "Fakturanr.: 1075" — the full stop used to make the parser give up here.

    It then fell through to the next label in the list, which is "ordrenummer",
    and a faktura came back numbered 79 after its order line.
    """
    found = read(ELEKTRA, today=TODAY)
    assert found.invoice_number == "1075"
    assert found.number_kind is None


def test_a_rate_in_a_two_row_column_heading_is_read() -> None:
    """A table header split over two lines is a label with no rate beside it."""
    found = read(ELEKTRA, today=TODAY)
    assert found.vat_rate == Decimal("0.25")
    # And with the gross known, the amount is arithmetic rather than a guess.
    assert found.vat_amount == Decimal("288.00")


# A FINN receipt: a private-seller purchase carrying no VAT, with two FINN fees
# under it at two different rates.
FINN = """"SEASONIC TX1600W Titanium"
Kjøpsdato:
30.08.2026, kl 10:08
Frakt med PostNord
109,00 kr
Mva. 25 %
Avgift for Trygg betaling
206,00 kr
Mva. 0 %
Totalt
3 565,00 kr
"""


def test_a_fee_is_not_vat() -> None:
    """ "Avgift" is the ordinary Norwegian word for a fee.

    Read as a VAT label, "Avgift for Trygg betaling 206,00 kr" made 206 kroner
    of deductible VAT appear on a purchase from a private individual.
    """
    assert read(FINN, today=TODAY).vat_amount != Decimal("206.00")


def test_a_labelled_amount_does_not_reach_over_a_line_with_words_on_it() -> None:
    """How the fee above was claimed: two lines below a rate that had no figure."""
    assert read(FINN, today=TODAY).vat_amount is None


def test_two_rates_on_one_bilag_propose_no_code_at_all() -> None:
    """25 % on the postage, 0 % on the fee, and no VAT on the thing itself.

    One entry cannot carry two rates. Proposing either would put a deduction on
    the whole 3 565 that does not exist, so the user is told what the document
    showed and asked to decide.
    """
    found = read(FINN, today=TODAY)
    assert found.vat_rate is None
    assert found.printed_rates == (Decimal("0.25"), Decimal("0"))

    proposal = suggest(FINN, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    apply_type(proposal, reading=found, type_code="varekjop", source=FieldSource.DOCUMENT)

    assert proposal.tax_code is None
    assert any("flere mva-satser" in warning for warning in proposal.warnings)


def test_a_bank_screenshot_states_its_amount_under_amount() -> None:
    """A payment confirmation is a bilag, and it says "Amount", not "Å betale"."""
    screenshot = "Anders A\n01. September 2026\nDetails\nAmount NOK 7 500,00\nBooked 02.09.2026"
    assert read(screenshot, today=TODAY).gross_amount == Decimal("7500.00")


def test_an_item_row_of_figure_columns_still_names_the_thing() -> None:
    """The Elektra invoice, as Løynd's extractor hands it over.

    Two things about this table beat the reader before: a two-line header over
    six columns puts seven unusable lines between "Beskrivelse" and the first
    item, and the item row is one word followed by five figures, which is mostly
    digits by any measure of length. The description that came out was "Faktura
    1075, LINJEFORENINGEN ELEKTRA" — the sender, because nothing else was found.
    """
    text = "\n".join(
        [
            "LINJEFORENINGEN ELEKTRA",
            "Fakturanr.: 1075",
            "Fakturadato: 2026-09-06",
            "Beskrivelse Antall Enh.pris",
            "(ekskl. mva)",
            "Beløp",
            "(ekskl. mva)",
            "Mva",
            "(25 %)",
            "Beløp",
            "(inkl. mva)",
            "Batteri 1 1 152,00 1 152,00 288,00 1 440,00",
            "Betales til bankkonto 4202.11.39496 NOK  1 440,00",
        ]
    )

    found = read(text, today=date(2026, 9, 6))

    assert found.item_text == "Batteri"
    suggestion = suggest(text, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME, reading=found)
    assert suggestion.description == "Faktura 1075, Batteri"


def test_a_field_label_under_a_heading_is_not_what_was_bought() -> None:
    """Reading further down the page costs something, and this is the price.

    The Brønnøysund invoice has "Fakturanummer" a few lines under its item
    heading and the FINN receipt has "Selger". Both are the right length, all
    letters, and free of figures, and both reached the ledger as the
    description of a purchase. A row of an item table has a price on it; a
    label does not.
    """
    text = "\n".join(
        [
            "Brønnøysundregistrene",
            "Beskrivelse",
            "Fakturanummer",
            "935091521",
            "Kundenummer",
            "Gebyr for registrering av aksjeselskap 1 6 825,00 0,00 6 825,00",
        ]
    )

    assert read(text, today=TODAY).item_text == "Gebyr for registrering av aksjeselskap"


def test_a_header_field_whose_value_was_stripped_is_not_an_item() -> None:
    """The Brønnøysund invoice, whose "BESKRIVELSE" heading sits above its header.

    Nothing under that heading is an item: the lines are the invoice's own
    fields, and "Ordredato 01.09.2026" survives every other test — right
    length, mostly letters, no noise word — until its date is stripped off and
    a bare label is left. It reached the ledger as what a 6 825-krone
    registration fee was for.
    """
    text = "\n".join(
        [
            "BESKRIVELSE",
            "F A K T U R A",
            "Fakturanummer: 935091521",
            "Ordrenr 5752816",
            "Ordredato 01.09.2026",
        ]
    )

    assert read(text, today=TODAY).item_text is None


def test_an_item_line_with_no_price_beside_it_is_still_read() -> None:
    """The fallback, and the receipt shape that needs it.

    A web receipt lists what was bought on its own line and its totals further
    down, so requiring a price on the same line would read nothing at all here.
    Preferring a priced row is a preference, not a requirement.
    """
    text = "\n".join(
        [
            "Item name",
            "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU",
            "Quantity 1",
            "Order total GBP 2,137.00",
        ]
    )

    assert read(text, today=TODAY).item_text == (
        "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU"
    )


def test_the_direction_the_user_chose_beats_the_documents() -> None:
    """An invoice we issued reads as money in; the user says it went out.

    Their word wins, the category is guessed from their side of it, and the
    direction is not marked as a proposal, because nobody proposed it.
    """
    read_as = suggest(OWN_SALE, today=TODAY, own_orgnr=OWN_ORGNR, own_name=OWN_NAME)
    assert read_as.direction is CashFlowDirection.INN

    chosen = suggest(
        OWN_SALE,
        today=TODAY,
        own_orgnr=OWN_ORGNR,
        own_name=OWN_NAME,
        direction=CashFlowDirection.UT,
    )

    assert chosen.direction is CashFlowDirection.UT
    assert "direction" not in chosen.sources
    if chosen.type_code is not None:
        assert CASH_FLOW_TYPES[chosen.type_code].direction is CashFlowDirection.UT


# --- One card charge for several orders (aliexpress2) ----------------------------

VOEC_INVOICE = """Tax Invoice
Invoice No. : NO011012026100500011117
Invoice Date : 2026-10-05
Sold By:  stemedu Official Store Alibaba.com Singapore E-Commerce Private Limited
VOEC ID: 2034638
ITEMS Qty Price (tax excl.) Discount Tax Rate Tax Amount Amount (USD)   Amount (NOK)
Order Number: 3076911632201854
75kg 5M Diving Servos IP68 Waterproof
1 42.21 - 25% 10.56 52.80 531
Amount paid 52.80 531
VAT%
Taxable Base
(in NOK)
VAT
(in NOK)
25% 425 106
Total 425 106
Alibaba.com Singapore E-Commerce Private Limited is raising this invoice in accordance to
the applicable tax laws for the low value goods supplied by 3rd party offshore sellers
"""

BANK_APP_CHARGE = """09:46
Transaction
Outgoing
06.10.26
-9 771,76 NOK
ALIEXPRESSCOM
From
4214 33 41180
Firmak
Booked date
06.10.26
"""

ORDER_SUMMARY = """AliExpress
Summary
Order ID:
3076911632181854
Subtotal:
5 939 kr
All discount:
30,19 kr
Total:
5 908,81 kr
Item detail
Glitter 811H 42KW Spot Welder Ma
5 939 kr
x1
"""


def test_a_voec_invoice_is_read_for_what_was_paid() -> None:
    """Not for "Total 425 106", which is a base of 425 and VAT of 106 side by side."""
    reading = read(VOEC_INVOICE, today=date(2026, 10, 9))

    assert reading.gross_amount == Decimal("531")


def test_private_limited_is_not_a_vat_line() -> None:
    """ "Private" contains "vat", and "3rd party" ends in a figure."""
    reading = read(VOEC_INVOICE, today=date(2026, 10, 9))

    assert reading.vat_amount != Decimal("3")


def test_a_subtotal_above_a_discount_is_not_the_total() -> None:
    reading = read(ORDER_SUMMARY, today=date(2026, 10, 9), has_letterhead=False)

    assert reading.gross_amount == Decimal("5908.81")


def test_a_bank_app_charge_covering_several_orders_is_the_bilag() -> None:
    """The kontonummer is spaced and the amount carries no label, and it is still the bank.

    Missing either made the nine orders compete for the total, and the largest
    of them was a VAT summary read as 425 106 kroner.
    """
    text = (
        f"{BANK_APP_CHARGE}\n--- Dokument 2: invoice.pdf ---\n{VOEC_INVOICE}"
        f"\n--- Dokument 3: summary.png ---\n{ORDER_SUMMARY}"
    )
    reading = read(text, today=date(2026, 10, 9), has_letterhead=False)

    assert reading.gross_amount == Decimal("9771.76")
    assert reading.total_is_negative is True


def test_an_unlabelled_bank_figure_smaller_than_the_purchase_is_another_movement() -> None:
    """The FINN bilag: the owner's 3 000 beside a 3 565 receipt is not what it cost."""
    text = (
        "Outgoing payment\n1520.34.91244\n30 August 2026\n-NOK 3 000,00\n"
        "\n--- Dokument 2: kvittering.png ---\nDu har betalt\nTotalt\n3 565 kr\n"
    )
    reading = read(text, today=date(2026, 9, 7), has_letterhead=False)

    assert reading.gross_amount == Decimal("3565")


def test_several_orders_are_not_described_by_the_first_one() -> None:
    """Different numbers and different items: no single one of them is the bilag."""
    text = (
        f"{BANK_APP_CHARGE}\n--- Dokument 2: invoice.pdf ---\n{VOEC_INVOICE}"
        f"\n--- Dokument 3: other.pdf ---\n"
        + VOEC_INVOICE.replace("11117", "11110").replace("Diving Servos", "Copper Foil Tape")
    )
    reading = read(text, today=date(2026, 10, 9), has_letterhead=False)

    assert reading.invoice_number is None
    assert reading.item_text is None


def test_voec_vat_is_never_proposed_as_input_vat() -> None:
    """The 25 % on a VOEC invoice is a consumer's VAT, and no business deducts it.

    The parser proposed no code here, so the form took the category's: 25 % on
    9 771,76 for a registered company, of which 333 kroner was VAT at all.
    """
    text = (
        f"{BANK_APP_CHARGE}\n--- Dokument 2: invoice.pdf ---\n{VOEC_INVOICE}"
        f"\n--- Dokument 3: summary.png ---\n{ORDER_SUMMARY}"
    )
    found = read(text, today=date(2026, 10, 9), has_letterhead=False)
    proposal = suggest(
        text, today=date(2026, 10, 9), own_orgnr=OWN_ORGNR, own_name=OWN_NAME, reading=found
    )
    apply_type(proposal, reading=found, type_code="varekjop", source=FieldSource.MODEL)

    assert found.under_voec
    assert proposal.tax_code == "0"
    assert proposal.sources["tax_code"] is FieldSource.DOCUMENT
    assert any("VOEC" in warning for warning in proposal.warnings)


def test_a_voec_invoice_in_dollars_is_not_called_free_of_norwegian_vat() -> None:
    """The currency rule says "ingen norsk mva", which a VOEC invoice contradicts."""
    text = "Tax Invoice\nVOEC ID: 2034638\nInvoice Date : 2026-10-05\nAmount paid USD 52.80\n"
    found = read(text, today=date(2026, 10, 9))
    proposal = suggest(
        text, today=date(2026, 10, 9), own_orgnr=OWN_ORGNR, own_name=OWN_NAME, reading=found
    )
    apply_type(proposal, reading=found, type_code="varekjop", source=FieldSource.MODEL)

    assert found.currency_code == "USD"
    assert proposal.tax_code == "0"
    assert not any("ingen norsk mva" in warning for warning in proposal.warnings)


def test_an_ordinary_norwegian_invoice_is_not_read_as_voec() -> None:
    assert not read(PURCHASE, today=TODAY).under_voec
    assert only(PURCHASE).tax_code == "1"


def test_a_camera_roll_name_says_nothing() -> None:
    assert interpretation.is_camera_roll("IMG 9461")
    assert not interpretation.is_camera_roll("Order details eBay")


#: A samlefaktura from a farm shop, laid out the way Coop's comes out of its text
#: layer: the payment line prints the kontonummer *after* the amount, with spaces,
#: and the register's name stands in front of the organisasjonsnummer.
SAMLEFAKTURA = """Faktura
Fakturanr / Fakturadato
9001 / 30.09.2026
Bankkonto
1503.84.20000
Vi tilbyr faktura på e-post og EHF. Ta kontakt:
E-post: regnskap@landhandel.example
Tlf: 51 00 00 00 - Butikkregnskap
Foretaksregisteret: NO910000020MVA
Fakturaavsender
BYGDELAGET LANDHANDEL SA
Postboks 12
7435 Trondheim
Fakturamottaker
NORDMANN CONSULTING AS
Fakturaer (bonger) i samlefaktura Netto Mva. Totalt Mva.%
Totalt for avgiftssats     12.000,00     3.000,00     15.000,00 25
 Totalt samlefaktura     12.000,00     3.000,00     15.000,00
NOK 15.000,00  betales til kontonummer 1503 84 20000
"""


def test_a_kontonummer_after_the_amount_is_not_the_amount() -> None:
    """Coop's payment line came back as an invoice of 20 000 kroner.

    The total is the last figure on a "betales" line, and the spaced kontonummer
    behind the amount read as three more figures, the last of them 20000.
    """
    assert amounts_in("NOK 15.000,00  betales til kontonummer 1503 84 20000") == [
        Decimal("15000.00")
    ]
    assert read(SAMLEFAKTURA, today=TODAY, own_orgnr=OWN_ORGNR).gross_amount == Decimal("15000.00")


def test_the_register_in_front_of_the_number_is_not_the_sender() -> None:
    """ "Foretaksregisteret: NO968712233MVA" named Coop and Tripletex alike.

    The label and the "NO" were alternatives in the pattern, so only the "NO"
    came off and the register's name was what was left of the line.
    """
    found = read(SAMLEFAKTURA, today=TODAY, own_orgnr=OWN_ORGNR)
    assert found.seller_name == "BYGDELAGET LANDHANDEL SA"
    assert _clean_name("Foretaksregisteret") is None


def test_a_vat_column_headed_with_the_percent_sign_is_read() -> None:
    """ "Netto Mva. Totalt Mva.%" over "... 15.000,00 25": the sign is in the heading."""
    found = read(SAMLEFAKTURA, today=TODAY, own_orgnr=OWN_ORGNR)
    assert found.vat_rate == Decimal("0.25")


def test_a_street_written_short_is_not_a_name() -> None:
    """ "Bedriftsvn 21" was Q-Meieriene's name in the books for one run."""
    assert _clean_name("Bedriftsvn 21") is None
    assert _clean_name("Meierivegen 4") is None
    assert _clean_name("Q-Meieriene AS") == "Q-Meieriene AS"


def test_an_orgnr_with_mva_behind_it_is_never_an_amount() -> None:
    """ "Foretagsreg: NO 913 251 342 MVA" was a slaughter settlement's VAT.

    The line carries «MVA», and the spaced number reads as nine hundred million
    kroner. Recognised by its shape and check digit, not by the label's spelling.
    """
    assert amounts_in("Foretagsreg: NO 910 000 039 MVA") == []
    assert amounts_in("Org.nr. 910.000.039 MVA  Sum 1 250,00") == [Decimal("1250.00")]
    assert read(PURCHASE, today=TODAY).vat_amount == Decimal("361.75")
