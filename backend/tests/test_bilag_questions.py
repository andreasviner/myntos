"""Asking, rather than warning.

A warning says something is wrong. A question says what to type — and the
register has somewhere for the answer to go, so a question is the difference
between a blank explanation box and one that gets filled in.

The split these tests are about: almost every question is derived from a fact
the register already established about its own reading. Two come from the
model — one from a second number it named on the category menu, one composed in
prose for the case no condition in code could anticipate — and both are marked
as the model's on the way out.
"""

from __future__ import annotations

from decimal import Decimal

from myntos.domain.bilag.interpretation import InvoiceReading, InvoiceSuggestion
from myntos.domain.bilag.questions import MAX_QUESTIONS, clean, compose
from myntos.domain.ledger.cashflow import CashFlowDirection, get_type


def suggestion(**fields: object) -> InvoiceSuggestion:
    proposal = InvoiceSuggestion()
    for name, value in fields.items():
        setattr(proposal, name, value)
    return proposal


def test_a_settled_bilag_is_asked_nothing() -> None:
    """Every question costs the user attention; a complete reading costs none."""
    asked = compose(
        suggestion(
            gross_amount=Decimal("1440.00"),
            type_code="varekjop",
            tax_code="1",
        ),
        InvoiceReading(vat_rate=Decimal("0.25")),
    )
    assert asked == []


def test_a_missing_amount_is_asked_about_first() -> None:
    """Nothing can be posted without it, so it outranks everything else."""
    asked = compose(suggestion(type_code=None), InvoiceReading())
    assert asked[0].field == "gross_amount"
    assert asked[0].text.endswith("?")


def test_a_foreign_bilag_is_asked_what_the_account_was_charged() -> None:
    """The printed total is not the entry; the bank's figure is.

    Card fees and the exchange spread sit between them, and only the statement
    knows the difference.
    """
    asked = compose(
        suggestion(foreign_amount=Decimal("2137.00"), currency_code="GBP"),
        InvoiceReading(),
    )
    # The figure carries a non-breaking thousands separator, the same way the
    # amount field writes it — so the question and the field read alike.
    assert "2\u00a0137,00 GBP" in asked[0].text
    assert asked[0].field == "gross_amount"


def test_money_in_from_a_stranger_is_the_question_the_paper_cannot_answer() -> None:
    """The `finn/` fixture, in one assertion.

    Three thousand kroner arriving from a private account is an owner's
    deposit, a repaid outlay, a loan or a sale. The bank screenshot is
    identical in all four cases.
    """
    asked = compose(
        suggestion(
            gross_amount=Decimal("3000.00"),
            direction=CashFlowDirection.INN,
            type_code="annen_inntekt",
        ),
        InvoiceReading(),
    )
    assert any("innskudd fra eier" in question.text for question in asked)


def test_somebody_who_has_already_explained_is_not_asked_to_explain() -> None:
    """Asking "what is this?" of a person who just told you loses their trust."""
    asked = compose(
        suggestion(
            gross_amount=Decimal("3000.00"),
            direction=CashFlowDirection.INN,
            type_code="annen_inntekt",
        ),
        InvoiceReading(),
        has_note=True,
    )
    assert not any("innskudd fra eier" in question.text for question in asked)


def test_two_vat_rates_are_asked_about_rather_than_resolved() -> None:
    """One entry carries one code. Which one is not ours to decide."""
    asked = compose(
        suggestion(gross_amount=Decimal("3565.00"), type_code="varekjop"),
        InvoiceReading(printed_rates=(Decimal("0.25"), Decimal("0"))),
    )
    assert any(question.field == "tax_code" for question in asked)
    assert any("25 % og 0 %" in question.text for question in asked)


def test_the_driftsmiddel_threshold_is_per_asset_and_only_the_buyer_knows() -> None:
    """Two machines at 25 278 kr each, 50 556 kr on one payment.

    Over the 30 000 threshold as one payment, under it as two assets. The
    register cannot tell those apart from a bank line, and the person who
    bought them can answer in a word.
    """
    asked = compose(
        suggestion(
            gross_amount=Decimal("50556.00"),
            direction=CashFlowDirection.UT,
            type_code="driftsmiddel",
            tax_code="0",
        ),
        InvoiceReading(),
    )
    assert any("én gjenstand" in question.text for question in asked)


def test_the_models_question_comes_last_and_is_marked_as_its() -> None:
    """Everything above is a fact about our reading; this is a judgement."""
    asked = compose(
        suggestion(gross_amount=Decimal("100.00")),
        InvoiceReading(),
        model_question="Skal dette føres på prosjektet eller på driften?",
    )
    assert asked[-1].asked_by_model is True
    assert all(not question.asked_by_model for question in asked[:-1])


def test_no_more_than_three_are_ever_shown() -> None:
    """A form that opens with six questions is a form nobody answers."""
    asked = compose(
        suggestion(direction=CashFlowDirection.INN),
        InvoiceReading(printed_rates=(Decimal("0.25"), Decimal("0"))),
        model_question="Og en ting til?",
    )
    assert len(asked) <= MAX_QUESTIONS


# --- what the model is allowed to ask ----------------------------------------


def test_a_question_must_be_a_question() -> None:
    assert clean("Er dette innskudd fra eier?") == "Er dette innskudd fra eier?"
    assert clean("Dette er nok et innskudd fra eier.") is None


def test_a_truncated_question_is_refused() -> None:
    """MAX_ANSWER_TOKENS cuts a rambling model off mid-sentence.

    A line that lost its question mark to the ceiling is a fragment, and the
    check that it ends in one is what turns the truncation into a refusal
    rather than a half-sentence on screen.
    """
    assert clean("Er dette et innskudd fra eier eller er det") is None


def test_a_list_of_questions_is_not_one_question() -> None:
    assert clean("Hva er dette? Og hvem er selgeren?") is None


def test_an_essay_is_refused() -> None:
    assert clean("Hvorfor " + "å " * 200 + "?") is None


# --- the category the model was torn about -----------------------------------


def test_two_defensible_categories_become_one_question() -> None:
    """The battery.

    An invoice whose item line reads "Batteri" and nothing else was filed under
    Drivstoff og bilkostnad. A car battery is a fair reading of that word and
    the page says nothing to rule it out, so the answer is not to fix the
    parser but to ask the one person who knows what they bought.
    """
    asked = compose(
        suggestion(gross_amount=Decimal("1440.00"), type_code="varekjop", tax_code="1"),
        InvoiceReading(vat_rate=Decimal("0.25")),
        alternative=get_type("bilkostnad"),
    )

    assert len(asked) == 1
    assert asked[0].text == (
        "Er dette «Varekjøp» eller «Drivstoff og bilkostnad»? Skriv gjerne hva som ble kjøpt."
    )
    assert asked[0].field == "type_code"
    # The doubt is the model's, and the interface owes the user that difference.
    assert asked[0].asked_by_model


def test_a_runner_up_that_is_the_chosen_category_is_not_a_doubt() -> None:
    asked = compose(
        suggestion(gross_amount=Decimal("1440.00"), type_code="varekjop", tax_code="1"),
        InvoiceReading(vat_rate=Decimal("0.25")),
        alternative=get_type("varekjop"),
    )
    assert asked == []


def test_a_runner_up_to_a_category_nobody_proposed_is_not_asked_about() -> None:
    """Nothing on the form to arbitrate between, so there is nothing to ask.

    The category question is about the value in the dropdown. Where no category
    came out of the reading at all, the question that belongs there is the open
    one — what was bought — and it is asked instead of this one.
    """
    asked = compose(
        suggestion(gross_amount=Decimal("1440.00"), tax_code="1"),
        InvoiceReading(vat_rate=Decimal("0.25")),
        alternative=get_type("bilkostnad"),
    )

    assert [question.text for question in asked] == [
        "Hva ble kjøpt, og hva bruker selskapet det til?"
    ]


def test_the_doubt_outranks_the_driftsmiddel_question() -> None:
    """Both are about type_code; the wrong account is the bigger mistake.

    A driftsmiddel warning is about one boundary within a category. A doubt
    between two categories is about which account the money lands in at all,
    and it also decides whether input VAT is deducted, so it is asked first.
    """
    asked = compose(
        suggestion(
            gross_amount=Decimal("50556.00"),
            type_code="varekjop",
            tax_code="1",
            direction=CashFlowDirection.UT,
        ),
        InvoiceReading(vat_rate=Decimal("0.25")),
        alternative=get_type("driftsmiddel"),
        model_question="Hva skal kortene brukes til?",
    )

    assert len(asked) == MAX_QUESTIONS
    assert asked[0].text.startswith("Er dette «Varekjøp»")
    # And the model's own sentence is still last of the three.
    assert asked[-1].text == "Hva skal kortene brukes til?"


def test_a_settlement_is_not_asked_whether_it_was_a_sale() -> None:
    """The document said the buyer wrote it; asking if it was an owner's deposit is noise."""
    asked = compose(
        suggestion(
            direction=CashFlowDirection.INN,
            gross_amount=Decimal("72500.00"),
            type_code="landbruk_melk",
            tax_code="3",
            issued_by_buyer=True,
        ),
        InvoiceReading(vat_rate=Decimal("0.25")),
    )
    assert asked == []
    unrecognised = compose(
        suggestion(
            direction=CashFlowDirection.INN,
            gross_amount=Decimal("72500.00"),
            type_code="landbruk_melk",
            tax_code="3",
        ),
        InvoiceReading(vat_rate=Decimal("0.25")),
    )
    assert [question.field for question in unrecognised] == ["type_code"]


def test_feed_over_the_threshold_is_not_asked_about_as_an_asset() -> None:
    """A month of kraftfôr is tens of thousands of kroner, and is eaten within the season."""
    feed = compose(
        suggestion(gross_amount=Decimal("64000.00"), type_code="landbruk_kraftfor", tax_code="1"),
        InvoiceReading(vat_rate=Decimal("0.25")),
    )
    assert feed == []
    # A purchase that may be a thing kept for years still is: two machines
    # at 25 278 each, over the 30 000 limit together and under it apart.
    goods = compose(
        suggestion(gross_amount=Decimal("50556.00"), type_code="varekjop", tax_code="0"),
        InvoiceReading(),
    )
    assert [question.field for question in goods] == ["type_code"]
