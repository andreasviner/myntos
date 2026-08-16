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
    menu = classification.build_menu()

    assert [item.number for item in menu] == list(range(1, len(CASH_FLOW_TYPES) + 1))
    assert {item.type_code for item in menu} == set(CASH_FLOW_TYPES)


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
    assert prompt.rstrip().endswith("Svar med kun tallet til kategorien som passer best.")


def test_only_the_top_of_a_long_document_is_sent() -> None:
    """Less of the user's business leaves the building, and the answer is no worse."""
    prompt = classification.build_prompt("x" * 9000, classification.build_menu())
    assert "x" * classification.MAX_DOCUMENT_CHARS in prompt
    assert "x" * (classification.MAX_DOCUMENT_CHARS + 1) not in prompt


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
    chosen = await classification.classify(DOCUMENT, ask=Answering(str(strom.number)))

    assert chosen is not None
    assert chosen.code == "strom"
    assert chosen.account_number == "6340"


async def test_an_empty_document_is_not_worth_asking_about() -> None:
    model = Answering("1")

    assert await classification.classify("   \n", ask=model) is None
    assert model.prompts == []


async def test_an_answer_outside_the_menu_is_discarded() -> None:
    model = Answering("Jeg er en språkmodell og kan ikke svare på dette.")
    assert await classification.classify(DOCUMENT, ask=model) is None


async def test_a_restricted_menu_is_the_one_the_model_is_shown() -> None:
    """A recognised customer means the expense half is never even offered."""
    model = Answering("1")
    await classification.classify(DOCUMENT, ask=model, allowed=CashFlowDirection.INN)

    assert "Varekjøp" not in model.prompts[0]
    assert "Salg med 25 % mva" in model.prompts[0]
