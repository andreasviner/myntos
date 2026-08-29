"""What the model is allowed to add to a reading, and what it is not.

The model is stubbed throughout: these tests are about our side of the
conversation. The rule they exist to hold in place is that an answer only counts
when the document backs it up — a date the model quotes must be a date the
document prints, a currency must be written on the page, a seller must be named
there. The one exception is the description, which is prose about the document
rather than a quote from it, and is marked as the model's for exactly that
reason.
"""

from datetime import date

import pytest

from myntos.domain.bilag import classification, extraction, interpretation

TODAY = date(2026, 8, 27)

# A receipt from a foreign webshop: no Fakturadato, no organisasjonsnummer, no
# "Å betale", a date written month first and a total in pounds. Every label our
# own parser looks for is absent, which is what the model is here for.
RECEIPT = """Order information
Buyer andreali_7
Seller daisyaus-0
Placed on Aug 23, 2026
Payment method Credit Card
Order total
1 item GBP 2,137.00
Shipping Free
Order total GBP 2,137.00
Items bought from daisyaus-0
Order number: 10-15068-44333
NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU
"""

ANSWER = """DATO: Aug 23, 2026
VALUTA: GBP
MOTPART: daisyaus-0
BESKRIVELSE: Apple Mac Studio til kontoret"""


class Answering:
    """A stand-in for the model that always says the same thing."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, *, system: str, prompt: str, max_tokens: int) -> str:
        del system, max_tokens
        self.prompts.append(prompt)
        return self.reply


def parse(reply: str, document: str = RECEIPT) -> extraction.Extraction:
    return extraction.parse_answer(reply, document_text=document, today=TODAY)


# --- Reading the answer ---------------------------------------------------------


def test_the_four_lines_are_read_and_checked_against_the_document() -> None:
    found = parse(ANSWER)

    assert found.occurred_on == date(2026, 8, 23)
    assert found.currency_code == "GBP"
    assert found.counterparty_name == "daisyaus-0"
    assert found.description == "Apple Mac Studio til kontoret"


def test_ukjent_is_an_answer_and_leaves_the_field_alone() -> None:
    found = parse("DATO: UKJENT\nVALUTA: UKJENT\nMOTPART: -\nBESKRIVELSE: ukjent")

    assert found.is_empty


def test_a_reply_that_is_not_an_answer_at_all_is_discarded_whole() -> None:
    """No shape, nothing kept. There is nothing here to half-believe."""
    assert parse("Beklager, jeg kan ikke hjelpe med dette.").is_empty


def test_bullets_and_stray_prose_around_the_lines_are_tolerated() -> None:
    """A small model that decorates its answer still answered it."""
    found = parse("Her er svaret:\n- DATO: Aug 23, 2026\n* VALUTA: GBP\nTakk!")

    assert found.occurred_on == date(2026, 8, 23)
    assert found.currency_code == "GBP"


# --- A date the model points at, read by our own parser -------------------------


def test_a_date_the_document_does_not_print_is_not_a_date() -> None:
    """The model may point at the page. It may not write on it."""
    assert parse("DATO: 01.01.2026").occurred_on is None


def test_a_date_the_document_prints_but_we_cannot_parse_is_dropped() -> None:
    assert parse("DATO: Order information").occurred_on is None


def test_the_model_settles_which_of_several_dates_is_the_right_one() -> None:
    """Where our own reading would have to guess, and would guess earliest."""
    document = "Kvittering\n01.02.2026 abonnement til 01.03.2026\nBelastet 15.02.2026\n"

    assert parse("DATO: 15.02.2026", document).occurred_on == date(2026, 2, 15)


def test_a_date_outside_the_years_a_bilag_can_have_is_refused() -> None:
    document = "Utstedt 14.03.1998 og skrevet av på 14.03.1998\n"

    assert parse("DATO: 14.03.1998", document).occurred_on is None


# --- Currency -------------------------------------------------------------------


def test_a_currency_the_document_never_mentions_is_refused() -> None:
    assert parse("VALUTA: EUR").currency_code is None


def test_three_letters_that_are_not_a_currency_are_refused() -> None:
    """The receipt says NEW, SSD and GPU. None of them is money."""
    assert parse("VALUTA: SSD").currency_code is None


def test_kroner_are_reported_as_kroner_when_the_document_says_so() -> None:
    document = "Kvittering\nTotalt 1 240,00 kr\n"

    assert parse("VALUTA: NOK", document).currency_code == "NOK"


# --- The seller -----------------------------------------------------------------


def test_a_seller_the_document_does_not_name_is_refused() -> None:
    assert parse("MOTPART: eBay Inc").counterparty_name is None


def test_a_name_broken_across_lines_still_counts_as_printed() -> None:
    """A PDF text layer breaks where the layout broke, not where the name did."""
    document = "Faktura\nBergen\nByggservice AS\nOrg.nr 910 000 004\n"

    assert parse("MOTPART: Bergen Byggservice AS", document).counterparty_name == (
        "Bergen Byggservice AS"
    )


# --- The description ------------------------------------------------------------


def test_the_description_is_the_one_field_that_need_not_be_quoted() -> None:
    found = parse("BESKRIVELSE: Datamaskin til kontoret")

    assert found.description == "Datamaskin til kontoret"


def test_a_description_longer_than_the_limit_is_cut_rather_than_dropped() -> None:
    found = parse(f"BESKRIVELSE: {'kontorrekvisita ' * 40}")

    assert found.description is not None
    assert len(found.description) <= extraction.MAX_DESCRIPTION_CHARS


# --- Merging into a reading -----------------------------------------------------


def test_the_model_fills_the_gaps_and_says_which_ones_it_filled() -> None:
    reading = interpretation.InvoiceReading()
    merged, filled = extraction.merge(
        reading,
        extraction.Extraction(
            occurred_on=date(2026, 8, 23), currency_code="GBP", counterparty_name="daisyaus-0"
        ),
    )

    assert merged.invoice_date == date(2026, 8, 23)
    assert merged.currency_code == "GBP"
    assert merged.seller_name == "daisyaus-0"
    assert filled == {"invoice_date", "currency_code", "seller_name"}


def test_what_the_document_printed_is_not_overruled_by_the_model() -> None:
    """The model is here for the gaps, not for a second opinion."""
    reading = interpretation.InvoiceReading(
        invoice_date=date(2026, 3, 14), date_is_labelled=True, seller_name="Telenor Norge AS"
    )
    merged, filled = extraction.merge(
        reading,
        extraction.Extraction(occurred_on=date(2026, 4, 1), counterparty_name="Telia"),
    )

    assert merged == reading
    assert filled == frozenset()


def test_a_date_we_only_guessed_at_is_the_models_to_correct() -> None:
    """The earliest date on a receipt covering a period is not the receipt's date."""
    document = """Kvittering
Abonnement 01.01.2026 - 31.12.2026
Belastet 15.06.2026
"""
    reading = interpretation.read(document, today=TODAY)
    assert reading.invoice_date == date(2026, 1, 1)
    assert not reading.date_is_labelled

    merged, filled = extraction.merge(reading, extraction.Extraction(occurred_on=date(2026, 6, 15)))

    assert merged.invoice_date == date(2026, 6, 15)
    assert filled == {"invoice_date"}


def test_kroner_from_the_model_leave_the_currency_unset() -> None:
    """None means kroner in a reading, and a bilag in kroner is the ordinary one."""
    merged, filled = extraction.merge(
        interpretation.InvoiceReading(), extraction.Extraction(currency_code="NOK")
    )

    assert merged.currency_code is None
    assert filled == frozenset()


# --- What the merged reading does to the proposal --------------------------------


def test_a_proposal_built_on_the_model_says_so_field_by_field() -> None:
    reading = interpretation.read(RECEIPT, today=TODAY)
    # As if our own parser had read neither: what the model adds is what is marked.
    stripped = interpretation.InvoiceReading(
        gross_amount=reading.gross_amount, invoice_number=reading.invoice_number
    )
    merged, filled = extraction.merge(
        stripped, extraction.Extraction(occurred_on=date(2026, 8, 23), currency_code="GBP")
    )
    suggestion = interpretation.suggest(
        RECEIPT,
        today=TODAY,
        own_orgnr="910000012",
        own_name="Nordmann Consulting AS",
        reading=merged,
        model_fields=filled,
    )

    assert suggestion.occurred_on == date(2026, 8, 23)
    assert suggestion.sources["occurred_on"] is interpretation.FieldSource.MODEL


def test_the_models_sentence_goes_behind_the_documents_own_reference() -> None:
    suggestion = interpretation.suggest(
        RECEIPT, today=TODAY, own_orgnr="910000012", own_name="Nordmann Consulting AS"
    )
    extraction.apply_description(
        suggestion, extraction.Extraction(description="Apple Mac Studio til kontoret")
    )

    assert suggestion.description == "Ordre 10-15068-44333, Apple Mac Studio til kontoret"
    assert suggestion.sources["description"] is interpretation.FieldSource.MODEL


def test_no_sentence_from_the_model_leaves_our_own_description_standing() -> None:
    suggestion = interpretation.suggest(
        RECEIPT, today=TODAY, own_orgnr="910000012", own_name="Nordmann Consulting AS"
    )
    extraction.apply_description(suggestion, extraction.Extraction())

    assert suggestion.description == "Ordre 10-15068-44333, daisyaus-0"
    assert suggestion.sources["description"] is interpretation.FieldSource.DOCUMENT


# --- Asking ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_document_is_in_the_prompt_and_the_answer_comes_back_checked() -> None:
    model = Answering(ANSWER)

    found = await extraction.extract(RECEIPT, ask=model, today=TODAY)

    assert "Apple Mac Studio" in model.prompts[0]
    assert "BESKRIVELSE" in model.prompts[0]
    assert found.occurred_on == date(2026, 8, 23)


@pytest.mark.asyncio
async def test_a_document_with_no_text_is_not_worth_asking_about() -> None:
    model = Answering(ANSWER)

    found = await extraction.extract("   \n\n", ask=model, today=TODAY)

    assert found.is_empty
    assert model.prompts == []


@pytest.mark.asyncio
async def test_only_the_first_part_of_a_long_document_is_sent() -> None:
    model = Answering(ANSWER)

    await extraction.extract("Faktura\n" + "x" * 50_000, ask=model, today=TODAY)

    assert len(model.prompts[0]) < classification.MAX_DOCUMENT_CHARS + 1_000


def test_a_printed_item_line_beats_the_models_sentence() -> None:
    """The parser wins where it read something, descriptions included.

    Not a preference for our own code: on the eBay receipt loynd-lite answers
    "1 item NEW Apple Mac Studio M3 Ultra … på Economy Int'l Postage GBP
    2,137.00", padding the item name with the shipping method and a figure that
    is already its own field. The model is here for the documents that print no
    item table.
    """
    suggestion = interpretation.InvoiceSuggestion(reference="Ordre 10-15068-44333")
    suggestion.description = "Ordre 10-15068-44333, Apple Mac Studio M3 Ultra"
    found = extraction.Extraction(
        description="1 item NEW Apple Mac Studio på Economy Int'l Postage GBP 2 137"
    )

    extraction.apply_description(suggestion, found, item_text="Apple Mac Studio M3 Ultra")

    assert suggestion.description == "Ordre 10-15068-44333, Apple Mac Studio M3 Ultra"
    assert "description" not in suggestion.sources


def test_the_model_still_writes_the_description_when_nothing_was_printed() -> None:
    """No item table, so the sentence is the only answer there is."""
    suggestion = interpretation.InvoiceSuggestion(reference="Faktura 104588")
    found = extraction.Extraction(description="Mobilabonnement for mars")

    extraction.apply_description(suggestion, found, item_text=None)

    assert suggestion.description == "Faktura 104588, Mobilabonnement for mars"
    assert suggestion.sources["description"] is interpretation.FieldSource.MODEL
