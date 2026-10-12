"""The numbered menu the model chooses from, and what happens when it does not.

The model is never trusted to be available, correct, or even coherent — every
test here is about what the register does with the answer, including the answers
that are not answers. Løynd itself is stubbed: these check our side of the
conversation, not theirs.
"""

import pytest

from myntos.domain.bilag import classification
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES, CashFlowDirection, types_for

DOCUMENT = "Elvia AS\nNettleie mars 2026\nÅ betale 1 240,00\n"


class Answering:
    """A stand-in for the model that always says the same thing."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, *, system: str, prompt: str, max_tokens: int) -> str:
        del system, max_tokens
        self.prompts.append(prompt)
        return self.reply


# --- The menu -------------------------------------------------------------------


def test_the_menu_offers_every_category_exactly_once() -> None:
    menu = classification.build_menu(segments=frozenset({"landbruk"}))

    assert [item.number for item in menu] == list(range(1, len(CASH_FLOW_TYPES) + 1))
    assert {item.type_code for item in menu} == set(CASH_FLOW_TYPES)


def test_only_a_farm_is_offered_the_farm_categories() -> None:
    """A model that can answer «Melkeoppgjør» for a hairdresser eventually will."""
    everyone = {item.type_code for item in classification.build_menu()}
    farm = {item.type_code for item in classification.build_menu(segments=frozenset({"landbruk"}))}

    assert "landbruk_melk" not in everyone
    assert "landbruk_melk" in farm
    assert everyone < farm


def test_a_menu_can_be_restricted_to_one_direction() -> None:
    """A recognised customer makes an expense category an impossible answer."""
    menu = classification.build_menu(CashFlowDirection.INN)

    assert {item.direction for item in menu} == {CashFlowDirection.INN}
    assert len(menu) == len(types_for(CashFlowDirection.INN))


def test_the_prompt_holds_the_document_and_the_numbered_choices() -> None:
    menu = classification.build_menu()
    prompt = classification.build_prompt(DOCUMENT, menu)

    assert "Nettleie mars 2026" in prompt
    assert "1. Salg med 25 % mva (penger inn)" in prompt
    assert "Svar med tallet til kategorien som passer best." in prompt
    # The instruction about doubt comes last, because the last line is the one a
    # small model attends to most closely.
    assert prompt.rstrip().endswith("skriv begge tallene med skråstrek mellom, som 9/20.")


def test_only_the_top_of_a_long_document_is_sent() -> None:
    """Less of the user's business leaves the building, and the answer is no worse."""
    prompt = classification.build_prompt("x" * 9000, classification.build_menu())
    assert "x" * classification.MAX_DOCUMENT_CHARS in prompt
    assert "x" * (classification.MAX_DOCUMENT_CHARS + 1) not in prompt


def test_every_document_on_a_bilag_is_shown() -> None:
    """Each file gets its own allowance, so the last order is read like the first.

    The first 2000 characters of nine AliExpress orders were the bank line and
    two invoices, and the model described the purchase as one servo.
    """
    text = "\n\n".join(f"--- Dokument {n}: {n}.pdf ---\n" + f"vare{n} " * 400 for n in range(1, 6))
    prompt = classification.build_prompt(text, classification.build_menu())

    for n in range(1, 6):
        assert f"vare{n}" in prompt


def test_the_whole_bilag_stops_at_the_ceiling() -> None:
    """Under 90 % of the context even at one character per token."""
    text = "\n\n".join(f"--- Dokument {n}: {n}.pdf ---\n" + f"{n:02d}" * 1000 for n in range(1, 30))
    shown, cut = classification.measure(text)

    assert cut
    assert shown == classification.MAX_BILAG_CHARS
    assert len(classification.excerpt(text)) == classification.MAX_BILAG_CHARS


def test_small_print_repeated_on_every_order_is_sent_once() -> None:
    boilerplate = "1. Items are sold by a third-party seller, not AliExpress."
    text = "\n".join(
        f"--- Dokument {n}: {n}.png ---\nTotal:\n{n}00 kr\n{boilerplate}" for n in range(1, 4)
    )
    shown = classification.excerpt(text)

    assert shown.count(boilerplate) == 1
    # A short label is kept in every document: the figure under it needs it.
    assert shown.count("Total:") == 3


# --- Reading the answer -----------------------------------------------------------


@pytest.mark.parametrize("reply", ["3", " 3 ", "3.", "Kategori 3", "3 — Salg med 12 % mva"])
def test_a_number_is_read_however_it_is_wrapped(reply: str) -> None:
    menu = classification.build_menu()
    chosen = classification.parse_choice(reply, menu)

    assert chosen is not None
    assert chosen.number == 3


@pytest.mark.parametrize("reply", ["", "0", "999", "vet ikke", "varekjøp"])
def test_anything_that_is_not_a_number_in_range_is_no_answer(reply: str) -> None:
    assert classification.parse_choice(reply, classification.build_menu()) is None


# --- Asking ------------------------------------------------------------------------


async def test_the_chosen_number_comes_back_as_a_category() -> None:
    strom = next(item for item in classification.build_menu() if item.type_code == "strom")
    answered = await classification.classify(DOCUMENT, ask=Answering(str(strom.number)))

    assert answered.chosen is not None
    assert answered.chosen.code == "strom"
    assert answered.chosen.account_number == "6340"
    # One number is not doubt, and must not produce a question.
    assert answered.alternative is None
    assert not answered.in_doubt


async def test_an_empty_document_is_not_worth_asking_about() -> None:
    model = Answering("1")
    answered = await classification.classify("   \n", ask=model)

    assert answered.chosen is None
    assert model.prompts == []


async def test_an_answer_outside_the_menu_is_discarded() -> None:
    model = Answering("Jeg er en språkmodell og kan ikke svare på dette.")
    answered = await classification.classify(DOCUMENT, ask=model)

    assert answered.chosen is None
    assert not answered.in_doubt


# --- Two numbers, meaning the model was torn ----------------------------------------


async def test_two_numbers_come_back_as_a_doubt() -> None:
    """The battery case: one word on an item line, two defensible accounts.

    "Batteri" was filed under Drivstoff og bilkostnad with no way for the model
    to say it was guessing, because one slot gets filled and a filled slot reads
    as certainty. Two slots let it say so, and the saying is what turns a silent
    misposting into a question.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    varekjop = next(item for item in menu if item.type_code == "varekjop")
    bilkostnad = next(item for item in menu if item.type_code == "bilkostnad")

    answered = await classification.classify(
        "Batteri 1 152,00",
        ask=Answering(f"{varekjop.number}/{bilkostnad.number}"),
        allowed=CashFlowDirection.UT,
    )

    assert answered.chosen is not None
    assert answered.chosen.code == "varekjop"
    assert answered.alternative is not None
    assert answered.alternative.code == "bilkostnad"
    assert answered.in_doubt


async def test_the_first_number_is_the_answer_even_when_the_second_is_nonsense() -> None:
    """A runner-up outside the menu is dropped without taking the answer down."""
    strom = next(item for item in classification.build_menu() if item.type_code == "strom")
    answered = await classification.classify(DOCUMENT, ask=Answering(f"{strom.number}/999"))

    assert answered.chosen is not None
    assert answered.chosen.code == "strom"
    assert answered.alternative is None


async def test_the_same_number_twice_is_one_answer() -> None:
    strom = next(item for item in classification.build_menu() if item.type_code == "strom")
    answered = await classification.classify(
        DOCUMENT, ask=Answering(f"{strom.number}/{strom.number}")
    )

    assert answered.alternative is None
    assert not answered.in_doubt


def test_only_two_numbers_are_read_out_of_a_reply() -> None:
    """Three is a shrug, not a doubt, and the third is not read."""
    menu = classification.build_menu()
    picked = classification.parse_choices("9/20/3/4", menu)

    assert [item.number for item in picked] == [9, 20]


async def test_a_restricted_menu_is_the_one_the_model_is_shown() -> None:
    """A recognised customer means the expense half is never even offered."""
    model = Answering("1")
    await classification.classify(DOCUMENT, ask=model, allowed=CashFlowDirection.INN)

    assert "Varekjøp" not in model.prompts[0]
    assert "Salg med 25 % mva" in model.prompts[0]


# --- A bilag that shows both legs is asking a narrower question ------------------


def test_a_return_is_offered_the_refunds_and_not_the_sales() -> None:
    """Both legs are printed on the page, so the question is a refund of what.

    On the full money-in menu the model answered "Salg med 25 % mva" for an eBay
    refund, which reports output VAT on a purchase that was cancelled.
    """
    menu = classification.build_menu(CashFlowDirection.INN, returning=True)

    assert menu
    assert all(code.startswith("refusjon_") for code in (item.type_code for item in menu))
    assert "refusjon_varekjop" in {item.type_code for item in menu}
    # Numbered from one over the narrowed list, so an answer of "1" means the
    # first refund and not the first sale.
    assert [item.number for item in menu] == list(range(1, len(menu) + 1))


def test_the_money_out_leg_of_a_return_keeps_its_own_menu() -> None:
    """The inference that works one way round does not work the other.

    Narrowing money out to the six kreditnotaer is the obvious mirror and it is
    wrong. Measured against the real eBay bilag, whose two legs are the same GBP
    2 137,00 and whose net 1 584,41 went out because the rate moved, that menu
    got "Kreditnota: annen inntekt" out of the model: a currency loss filed as
    revenue given back. Money in with a matching outgoing leg cannot be a sale;
    money out with a matching incoming leg can be almost anything, so the whole
    menu stays on offer and `valutatap` stays reachable.
    """
    menu = classification.build_menu(CashFlowDirection.UT, returning=True)

    assert {item.type_code for item in menu} == {
        item.type_code for item in classification.build_menu(CashFlowDirection.UT)
    }
    assert "valutatap" in {item.type_code for item in menu}
    assert "kreditnota_salg_hoy_sats" in {item.type_code for item in menu}
