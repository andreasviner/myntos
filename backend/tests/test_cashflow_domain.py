"""Pure checks on the cash-flow catalogue, the orgnr rule and the gross split.

No database. These are the parts that have to be right before any endpoint can
be, and they are cheap enough to assert exhaustively — every catalogue entry,
every rate, a decade of amounts.
"""

from decimal import Decimal

import pytest

from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.errors import UnsupportedDocumentError
from myntos.domain.bilag.models import ACCEPTED_CONTENT_TYPES, PDF_CONTENT_TYPE
from myntos.domain.ledger import posting
from myntos.domain.ledger.cashflow import (
    CASH_FLOW_TYPES,
    CashFlowDirection,
    get_type,
    types_for,
)
from myntos.domain.ledger.chart import (
    ACCOUNTS_BY_NUMBER,
    KNOWN_ACCOUNTS,
    LANDBRUK_ACCOUNTS,
    NO_VAT_CODE,
    STANDARD_ACCOUNTS,
    STANDARD_TAX_CODES,
    TAX_CODES_BY_CODE,
)
from myntos.domain.ledger.errors import InvalidOrgnrError, UnknownCashFlowTypeError
from myntos.domain.ledger.models import NON_REPORTABLE_STANDARD_TAX_CODES
from myntos.domain.ledger.orgnr import validate_orgnr

# Every add-on a category can belong to. The invariants below hold for all of
# them; the catalogue a company without add-ons sees is ``types_for()``.
EVERY_SEGMENT = frozenset({"landbruk"})

# The rates the catalogue can produce, plus the boundary ones.
RATES = [Decimal("0.00"), Decimal("0.12"), Decimal("0.15"), Decimal("0.25")]


def decimal_places(value: Decimal) -> int:
    """How many digits after the point — ``Decimal.as_tuple`` also types NaN."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) else 0


# --- The catalogue --------------------------------------------------------------


def test_every_type_points_at_a_seeded_account_and_tax_code() -> None:
    """A category the seeded chart cannot serve is a 500 waiting for a user."""
    for entry in types_for():
        assert entry.account_number in ACCOUNTS_BY_NUMBER, entry.code
        assert entry.default_tax_code in TAX_CODES_BY_CODE, entry.code


def test_every_add_on_type_points_at_an_account_its_add_on_brings() -> None:
    """A farm category books to a farm account or one everybody has, never a stranger."""
    for entry in types_for(segments=EVERY_SEGMENT):
        assert entry.account_number in KNOWN_ACCOUNTS, entry.code
        assert entry.default_tax_code in TAX_CODES_BY_CODE, entry.code


def test_account_numbers_are_unique() -> None:
    numbers = [account.number for account in (*STANDARD_ACCOUNTS, *LANDBRUK_ACCOUNTS)]
    assert len(numbers) == len(set(numbers))


def test_both_directions_are_offered() -> None:
    assert types_for(CashFlowDirection.INN)
    assert types_for(CashFlowDirection.UT)
    assert len(types_for(segments=EVERY_SEGMENT)) == len(CASH_FLOW_TYPES)


def test_a_company_without_add_ons_sees_none_of_their_categories() -> None:
    """Myntos is for every business: a farm's categories are the farm's alone."""
    assert all(entry.segment is None for entry in types_for())
    farm = [entry for entry in types_for(segments=EVERY_SEGMENT) if entry.segment == "landbruk"]
    assert farm
    # Everyone's categories come first and in the order they always had, so
    # switching the add-on on only adds to the end of the list.
    assert types_for(segments=EVERY_SEGMENT)[: len(types_for())] == types_for()


def test_the_income_statement_is_only_ever_touched_from_its_own_side() -> None:
    """Revenue is only credited by a sale, and a cost only debited by a purchase.

    The invariant this replaces was "money in must not land on a cost account",
    which was the right rule while the bank's direction and the trade's were the
    same thing. Two families of category broke that: a refund is money in that
    reduces a cost, and a kreditnota is money out that reduces revenue. Testing
    ``direction`` would now have to except both, which excepts exactly the cases
    worth checking.

    ``books_as`` is the distinction, so the rule is stated against that and
    holds for all four combinations. The balance sheet is left out on purpose:
    an asset or a debt account legitimately moves either way — a driftsmiddel is
    bought and sold, a loan is drawn and repaid — and the sign is the posting's
    job, not the catalogue's.
    """
    for entry in types_for(segments=EVERY_SEGMENT):
        account = KNOWN_ACCOUNTS[entry.account_number]
        if account.account_type.value == "inntekt":
            assert entry.books_as is CashFlowDirection.INN, entry.code
        if account.account_type.value == "kostnad":
            assert entry.books_as is CashFlowDirection.UT, entry.code


def test_books_as_follows_the_bank_unless_the_entry_is_a_reversal() -> None:
    """The one rule the VAT side is picked from."""
    assert get_type("varekjop").books_as is CashFlowDirection.UT
    assert get_type("salg_hoy_sats").books_as is CashFlowDirection.INN
    # Money in, but a purchase for the purposes of the books and the mva-melding.
    assert get_type("refusjon_varekjop").books_as is CashFlowDirection.UT
    # Money out, but a sale.
    assert get_type("kreditnota_salg_hoy_sats").books_as is CashFlowDirection.INN


def test_a_refund_lands_exactly_where_its_purchase_did() -> None:
    """The invariant that replaces "money in never touches a cost account".

    A refund credits the account the purchase debited, so the cost goes down
    instead of turnover going up. If the two ever point at different accounts,
    both the cost and the VAT deduction stay overstated and nothing in the books
    says so, which is why this is derived rather than written out by hand.
    """
    # By name rather than by ``is_refund``, which now holds for the six
    # kreditnotaer too: those reverse a sale and are checked just below.
    refunds = [entry for entry in CASH_FLOW_TYPES.values() if entry.code.startswith("refusjon_")]
    assert refunds

    for entry in refunds:
        assert entry.direction is CashFlowDirection.INN, entry.code
        purchase = CASH_FLOW_TYPES[entry.reverses or ""]
        assert purchase.direction is CashFlowDirection.UT, entry.code
        assert entry.account_number == purchase.account_number, entry.code
        assert entry.default_tax_code == purchase.default_tax_code, entry.code
        assert entry.code == f"refusjon_{purchase.code}"


def test_a_kreditnota_lands_exactly_where_its_sale_did() -> None:
    """The mirror of the refund invariant, for money paid back to a customer.

    A kreditnota debits the account the sale credited, so turnover goes down
    instead of a cost going up. Booked anywhere else — and before these existed
    the only money-out categories were costs — the sale stays in the turnover
    the næringsspesifikasjon reports and the output VAT stays owed on it.
    """
    notes = [entry for entry in CASH_FLOW_TYPES.values() if entry.code.startswith("kreditnota_")]
    assert notes

    for entry in notes:
        assert entry.is_refund, entry.code
        assert entry.direction is CashFlowDirection.UT, entry.code
        sale = CASH_FLOW_TYPES[entry.reverses or ""]
        assert sale.direction is CashFlowDirection.INN, entry.code
        assert entry.account_number == sale.account_number, entry.code
        assert entry.default_tax_code == sale.default_tax_code, entry.code
        assert entry.code == f"kreditnota_{sale.code}"


def test_a_reversal_is_never_derived_from_another_reversal() -> None:
    """Both families come off the hand-written base, and nothing off each other.

    Deriving refunds from every money-out category would otherwise pick up the
    six kreditnotaer and produce "Refusjon: kreditnota: salg med 25 % mva",
    which is a sale, spelled as a reversal of a reversal.
    """
    for entry in types_for(segments=EVERY_SEGMENT):
        if entry.reverses is None:
            continue
        assert CASH_FLOW_TYPES[entry.reverses].reverses is None, entry.code


def test_settling_a_debt_is_neither_income_nor_cost() -> None:
    """Paying VAT, paying withheld tax and repaying a loan all move no result.

    Each of these was previously only recordable as a cost, which deducts money
    that was either never the company's (forskuddstrekk), already deducted
    (mva), or spent once already (the loan's principal).
    """
    for code in ("mva_oppgjor", "mva_tilgode", "forskuddstrekk", "avdrag_lan", "laneopptak"):
        entry = get_type(code)
        assert not entry.is_income_statement, code
        assert ACCOUNTS_BY_NUMBER[entry.account_number].account_type.value == "gjeld", code
        # None of them is a transaction with a VAT treatment: the tax on the
        # underlying trade was settled when the trade was booked.
        assert entry.default_tax_code == NO_VAT_CODE, code


def test_a_currency_difference_is_a_finance_line_and_not_a_purchase() -> None:
    """Where the eBay bilag's 1 584,41 belongs.

    "Annen driftskostnad" was the nearest thing before, and it puts an exchange
    rate movement among the driftskostnader the næringsspesifikasjon adds up as
    running expenses — and at 25 % input VAT, with a deduction nobody was
    charged.
    """
    loss, gain = get_type("valutatap"), get_type("valutagevinst")

    assert (loss.account_number, gain.account_number) == ("8160", "8060")
    assert loss.default_tax_code == NO_VAT_CODE
    assert gain.default_tax_code == NO_VAT_CODE
    assert ACCOUNTS_BY_NUMBER["8160"].grouping_category == "finanskostnad"
    assert ACCOUNTS_BY_NUMBER["8060"].grouping_category == "finansinntekt"


def test_selling_a_driftsmiddel_leaves_the_balance_sheet_rather_than_earning() -> None:
    """Used equipment sold is an asset going out, not turnover coming in.

    Booked to 3000 it would count as revenue while the thing sold stayed on
    1250 being depreciated, so the same value would be taxed twice: once as
    income now and once as a deduction that no longer has an asset behind it.
    The VAT is real though, and it is output VAT: a sale of business equipment
    is an avgiftspliktig omsetning like any other.
    """
    entry = get_type("salg_driftsmiddel")

    assert entry.account_number == "1250"
    assert not entry.is_income_statement
    assert entry.default_tax_code == "3"
    assert entry.books_as is CashFlowDirection.INN


def test_a_refund_of_a_driftsmiddel_goes_back_to_the_balance_sheet() -> None:
    """Money in landing on an asset account, which used to be impossible here.

    Buying a driftsmiddel puts 1250 on the balance sheet rather than in the
    income statement. Sending it back has to take it off the same account: a
    refund booked to 3900 would leave the asset standing and invent revenue.
    """
    refund = CASH_FLOW_TYPES["refusjon_driftsmiddel"]

    assert refund.account_number == "1250"
    assert ACCOUNTS_BY_NUMBER["1250"].account_type.value == "eiendel"
    assert not refund.is_income_statement


def test_every_type_carries_a_note() -> None:
    """The note is where the default VAT assumption is stated; an empty one lies."""
    for entry in types_for(segments=EVERY_SEGMENT):
        assert entry.note.strip()


def test_owner_movements_stay_out_of_the_income_statement() -> None:
    """A withdrawal is not a cost and a deposit is not income."""
    assert not get_type("privatuttak").is_income_statement
    assert not get_type("egenkapitalinnskudd").is_income_statement
    assert get_type("varekjop").is_income_statement


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(UnknownCashFlowTypeError):
        get_type("kjop_av_enhjorning")


def test_excluded_tax_codes_are_marked_non_reportable() -> None:
    """Codes 0, 7, 20, 21 and 22 must never reach the MVA-melding."""
    for seed in STANDARD_TAX_CODES:
        expected = seed.standard_tax_code not in NON_REPORTABLE_STANDARD_TAX_CODES
        assert seed.is_reportable is expected, seed.code


def test_zero_rated_sales_codes_are_still_reportable() -> None:
    """0 % is not the same as "outside the system" — the melding wants the basis."""
    for code in ("5", "6", "52"):
        seed = TAX_CODES_BY_CODE[code]
        assert seed.rate == Decimal("0.000000")
        assert seed.is_reportable


# --- Organisasjonsnummer --------------------------------------------------------


@pytest.mark.parametrize("orgnr", ["910000004", "910000012", "910000020", "910 000 039"])
def test_valid_orgnr_is_accepted_and_normalised(orgnr: str) -> None:
    assert validate_orgnr(orgnr) == orgnr.replace(" ", "")


@pytest.mark.parametrize(
    "orgnr",
    [
        "910000005",  # right length, wrong check digit
        "91000000",  # eight digits
        "9100000045",  # ten digits
        "91000000a",
        "",
        "٩٧٤٧٦٠٦٧٣",  # 974760673 in Arabic-Indic digits: a second spelling of a real orgnr
        "97476067³",  # isdigit() is true, int() is not
    ],
)
def test_invalid_orgnr_is_rejected(orgnr: str) -> None:
    with pytest.raises(InvalidOrgnrError):
        validate_orgnr(orgnr)


# --- Splitting a gross amount ---------------------------------------------------


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize(
    "gross",
    ["0.01", "0.99", "1.00", "12.34", "99.99", "1250.00", "1000.01", "123456.78"],
)
def test_split_gross_always_adds_back_to_the_gross(gross: str, rate: Decimal) -> None:
    """The invariant the whole register rests on: the voucher must balance."""
    amount = Decimal(gross)
    net, vat = posting.split_gross(amount, rate)
    assert net + vat == amount
    assert net >= Decimal("0.00")
    assert vat >= Decimal("0.00")
    assert decimal_places(net) <= 2
    assert decimal_places(vat) <= 2


def test_split_gross_on_a_round_amount() -> None:
    assert posting.split_gross(Decimal("1250.00"), Decimal("0.25")) == (
        Decimal("1000.00"),
        Decimal("250.00"),
    )


def test_split_gross_at_zero_rate_is_all_net() -> None:
    assert posting.split_gross(Decimal("500.00"), Decimal("0.00")) == (
        Decimal("500.00"),
        Decimal("0.00"),
    )


def test_split_gross_rejects_a_negative_rate() -> None:
    with pytest.raises(posting.PostingError):
        posting.split_gross(Decimal("100.00"), Decimal("-0.25"))


def test_split_gross_rejects_sub_ore_input() -> None:
    with pytest.raises(posting.PostingError):
        posting.split_gross(Decimal("100.001"), Decimal("0.25"))


# --- What may be a bilag ------------------------------------------------------


def test_a_pdf_is_recognised_by_its_bytes() -> None:
    assert bilag_service.validate_document(b"%PDF-1.7\n%...") == "application/pdf"


def test_a_photograph_is_a_bilag_now() -> None:
    """Most of a small company's purchases are documented by a phone camera.

    Bokføringsforskriften § 5-1 says what documentation must contain, never what
    file format it arrives in. Accepting PDF alone meant the archive served the
    third of a company's bilag that came from a supplier with an invoicing
    system and told everyone else to find a printer.
    """
    assert bilag_service.validate_document(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"
    assert bilag_service.validate_document(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert bilag_service.validate_document(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert bilag_service.validate_document(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"


def test_the_upload_does_not_get_to_say_what_it_is() -> None:
    """A .png that is really a script is refused on its bytes, not its name.

    The old check trusted the PDF magic number for exactly this reason, and
    widening the list of accepted formats must not widen the list of things we
    take somebody's word for.
    """
    with pytest.raises(UnsupportedDocumentError):
        bilag_service.validate_document(b"<?php system($_GET[0]); ?>")


def test_a_webp_needs_the_tag_and_not_just_the_container() -> None:
    """RIFF alone is also a WAV, an AVI and a handful of other things."""
    with pytest.raises(UnsupportedDocumentError):
        bilag_service.validate_document(b"RIFF\x00\x00\x00\x00WAVE")


def test_a_fallback_name_gets_the_right_extension() -> None:
    """A PNG stored as "bilag.pdf" is a row an auditor downloads and cannot open."""
    assert bilag_service.sanitise_filename("", content_type="image/png") == "bilag.png"
    assert bilag_service.sanitise_filename("", content_type="application/pdf") == "bilag.pdf"


def test_the_archive_and_the_reader_accept_the_same_formats() -> None:
    """A bilag that can be filed and never read would be a trap.

    The two lists live in different modules for good reasons — one is about what
    we keep for five years, the other about what Løynd can transcribe — and this
    is what stops them drifting apart.

    The rule is no longer that they are the same list. HEIC is kept by the
    archive and not opened by Løynd, so it is read through a JPEG copy instead
    (``READ_AS``); what has to hold is that every accepted image reaches Løynd
    one way or the other, and that nothing is converted into a format Løynd
    does not take either.
    """
    from myntos.domain.bilag.models import READ_AS
    from myntos.integrations.loynd import IMAGE_CONTENT_TYPES

    images = ACCEPTED_CONTENT_TYPES - {PDF_CONTENT_TYPE}
    assert images == IMAGE_CONTENT_TYPES | set(READ_AS)
    assert set(READ_AS.values()) <= IMAGE_CONTENT_TYPES
