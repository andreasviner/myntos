"""What the invoice form does with a model's proposed lines.

Same stance as ``test_bilag_classification``: the model is never trusted to be
available, correct or coherent, and every test here is about our side of the
conversation. The proposal reaches a draft only through a person, so what
matters is that a bad answer costs the user a blank field rather than a wrong
number on a document they then issue.
"""

from decimal import Decimal

import pytest

from myntos.domain.faktura import drafting
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS

SALE = "Solgte 10 timer rådgivning i mars til 1 500 kroner timen, pluss to kursmapper à 450."


class Answering:
    """A stand-in for the model that always says the same thing."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, *, system: str, prompt: str, max_tokens: int) -> str:
        del system, max_tokens
        self.prompts.append(prompt)
        return self.reply


def menu() -> tuple[drafting.TaxChoice, ...]:
    return drafting.build_tax_menu()


# --- The menu ---------------------------------------------------------------------


def test_the_menu_offers_every_sellable_treatment_exactly_once() -> None:
    choices = menu()

    assert [choice.number for choice in choices] == list(range(1, len(SALES_TAX_OPTIONS) + 1))
    assert {choice.tax_code for choice in choices} == {o.tax_code for o in SALES_TAX_OPTIONS}


def test_a_company_outside_the_vat_register_is_offered_no_taxable_rate() -> None:
    """§ 15-14: charging output VAT unregistered is not a formality to catch later."""
    choices = drafting.build_tax_menu(("0",))

    assert [choice.tax_code for choice in choices] == ["0"]


def test_a_code_outside_the_catalogue_cannot_be_asked_onto_the_menu() -> None:
    """``1`` is inngående mva. A caller asking for it gets the full menu, not it."""
    assert {choice.tax_code for choice in drafting.build_tax_menu(("3", "1"))} == {"3"}


def test_the_prompt_holds_the_description_the_format_and_the_menu() -> None:
    prompt = drafting.build_prompt(SALE, menu())

    assert "10 timer rådgivning" in prompt
    assert "beskrivelse | antall | enhet | pris per enhet | mva" in prompt
    assert "1. 25 % — alminnelig sats" in prompt
    assert prompt.rstrip().endswith("Svar med kun linjene.")


def test_only_the_first_part_of_a_long_description_is_sent() -> None:
    prompt = drafting.build_prompt("x" * 9000, menu())

    assert "x" * drafting.MAX_REQUEST_CHARS in prompt
    assert "x" * (drafting.MAX_REQUEST_CHARS + 1) not in prompt


# --- Reading the answer -------------------------------------------------------------


def test_a_well_formed_answer_becomes_lines() -> None:
    proposal = drafting.parse_lines(
        "Rådgivning mars | 10 | timer | 1 500,00 | 1\nKursmappe | 2 | stk | 450 | 1",
        menu(),
    )

    assert [line.description for line in proposal.lines] == ["Rådgivning mars", "Kursmappe"]
    first = proposal.lines[0]
    assert first.quantity == Decimal(10)
    assert first.unit_code == "HUR"
    assert first.unit_price == Decimal("1500.00")
    assert first.tax_code == "3"
    assert proposal.warnings == []


@pytest.mark.parametrize(
    ("word", "code"),
    [("time", "HUR"), ("timer", "HUR"), ("stk", "EA"), ("måned", "MON"), ("mnd", "MON")],
)
def test_the_unit_is_read_as_the_word_a_person_would_write(word: str, code: str) -> None:
    assert drafting.unit_for(word) == code


def test_an_unknown_unit_falls_back_rather_than_losing_the_line() -> None:
    proposal = drafting.parse_lines("Rådgivning | 3 | klumper | 900 | 1", menu())

    assert proposal.lines[0].unit_code == "EA"
    assert proposal.lines[0].description == "Rådgivning"


def test_prose_around_the_answer_is_ignored_without_costing_a_line() -> None:
    reply = (
        "Her er fakturalinjene:\n"
        "- Rådgivning mars | 10 | timer | 1500 | 1\n"
        "\n"
        "Si fra om du vil ha dem satt opp annerledes."
    )
    proposal = drafting.parse_lines(reply, menu())

    assert len(proposal.lines) == 1
    assert proposal.warnings == []


def test_the_echoed_format_line_is_not_a_sale() -> None:
    reply = "beskrivelse | antall | enhet | pris | mva\nRådgivning | 10 | timer | 1500 | 1"
    proposal = drafting.parse_lines(reply, menu())

    assert [line.description for line in proposal.lines] == ["Rådgivning"]


def test_a_row_with_the_wrong_number_of_fields_is_dropped_and_counted() -> None:
    """Which field is missing is unknowable, and guessing would misplace a price."""
    proposal = drafting.parse_lines("Rådgivning | 10 | 1500 | 1", menu())

    assert proposal.lines == []
    assert any("1 av linjene" in warning for warning in proposal.warnings)


def test_an_unreadable_price_keeps_the_line_and_says_so() -> None:
    """The description is the part the user would otherwise have to retype."""
    proposal = drafting.parse_lines("Rådgivning mars | 10 | timer | etter avtale | 1", menu())

    assert proposal.lines[0].unit_price == Decimal(0)
    assert any("prisen" in warning for warning in proposal.warnings)


def test_a_missing_quantity_becomes_one() -> None:
    proposal = drafting.parse_lines("Nettside | | stk | 25000 | 1", menu())

    assert proposal.lines[0].quantity == Decimal(1)


def test_a_vat_number_outside_the_menu_falls_back_and_is_flagged() -> None:
    proposal = drafting.parse_lines("Rådgivning | 1 | stk | 1000 | 99", menu())

    assert proposal.lines[0].tax_code == SALES_TAX_OPTIONS[0].tax_code
    assert any("Mva-satsen" in warning for warning in proposal.warnings)


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("1", "3"),  # the menu number, which is what was asked for
        ("25%", "3"),  # the rate, which is what a model shown "1. 25 %" often answers
        ("25 %", "3"),
        ("25", "3"),  # a bare rate: no menu entry has that number
        ("mva 2", "31"),  # menu number inside prose
        ("12 %", "32"),
        ("0 %", "5"),  # the literal reading: avgiftsfritt salg
    ],
)
def test_the_vat_treatment_is_read_as_a_menu_number_or_as_a_rate(field: str, code: str) -> None:
    """Observed against the real model, which answers "25%" rather than "1"."""
    chosen = drafting.tax_for(field, menu())

    assert chosen is not None
    assert chosen.tax_code == code


def test_a_percent_sign_means_the_number_is_a_rate_and_not_a_choice() -> None:
    """Otherwise "3 %" would silently become menu entry 3, which is 12 %."""
    assert drafting.tax_for("3 %", menu()) is None
    assert drafting.tax_for("3", menu()) is not None


def test_a_rate_is_only_read_from_the_menu_on_offer() -> None:
    """An unregistered company answering "25 %" gets no taxable line out of it."""
    restricted = drafting.build_tax_menu(("0",))

    assert drafting.tax_for("25 %", restricted) is None
    assert drafting.tax_for("1", restricted) == restricted[0]


def test_numbers_are_rounded_to_what_the_columns_hold() -> None:
    """Otherwise the service layer refuses the line and blames the user for it."""
    proposal = drafting.parse_lines("Strøm | 1,1234567 | stk | 0,123456 | 1", menu())

    assert proposal.lines[0].quantity == Decimal("1.123457")
    assert proposal.lines[0].unit_price == Decimal("0.1235")


def test_an_absurd_quantity_is_not_a_quantity() -> None:
    proposal = drafting.parse_lines("Rådgivning | 99999999 | timer | 1500 | 1", menu())

    assert proposal.lines[0].quantity == Decimal(1)


def test_no_more_lines_come_back_than_we_take() -> None:
    reply = "\n".join(f"Vare {n} | 1 | stk | 100 | 1" for n in range(drafting.MAX_LINES + 5))
    proposal = drafting.parse_lines(reply, menu())

    assert len(proposal.lines) == drafting.MAX_LINES
    assert any("5 av linjene" in warning for warning in proposal.warnings)


# --- Asking ---------------------------------------------------------------------------


async def test_the_answer_comes_back_as_a_proposal() -> None:
    model = Answering("Rådgivning mars | 10 | timer | 1500 | 1")
    proposal = await drafting.draft_lines(SALE, ask=model)

    assert len(proposal.lines) == 1
    assert "10 timer rådgivning" in model.prompts[0]


async def test_an_empty_description_is_not_worth_asking_about() -> None:
    model = Answering("Noe | 1 | stk | 100 | 1")

    assert await drafting.draft_lines("   \n", ask=model) == drafting.LineProposal()
    assert model.prompts == []


async def test_an_answer_that_is_not_lines_leaves_the_form_to_the_user() -> None:
    model = Answering("Jeg er en språkmodell og kan dessverre ikke hjelpe med dette.")
    proposal = await drafting.draft_lines(SALE, ask=model)

    assert proposal.lines == []
    assert proposal.warnings[0].startswith("Vi fikk ikke laget fakturalinjer")


async def test_a_restricted_menu_is_the_one_the_model_is_shown() -> None:
    model = Answering("Klipp | 1 | stk | 500 | 1")
    proposal = await drafting.draft_lines(SALE, ask=model, allowed_tax_codes=("0",))

    assert "25 % — alminnelig sats" not in model.prompts[0]
    assert proposal.lines[0].tax_code == "0"


async def test_a_gross_price_in_the_description_is_pointed_out() -> None:
    """A line priced inclusive of VAT is 25 % too high, and nothing else would say so."""
    model = Answering("Klipp | 1 | stk | 500 | 1")
    proposal = await drafting.draft_lines("Klipp til 500 kr inkl. mva", ask=model)

    assert any("inkludert mva" in warning for warning in proposal.warnings)
