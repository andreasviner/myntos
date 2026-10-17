"""Answering a question by continuing the conversation, not by starting it again.

What these hold: the follow-up repeats the first question and the model's answer
to it exactly, so the model server can reuse its work on the bilag and the model
can see what it asked; what the user said goes in one new turn; and nothing the
user says can put a date or a seller on the form that the documents do not print.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any

from myntos.domain.bilag import classification, extraction, followup
from myntos.domain.ledger.cashflow import CashFlowDirection

TODAY = date(2026, 10, 17)

RECEIPT = """Kvittering
Elkjøp Lørenskog
17.10.2026
Skjermkort RTX 5070
Totalt 6 990,00 kr
"""

FIRST_ANSWER = """DATO: 17.10.2026
VALUTA: NOK
MOTPART: Elkjøp Lørenskog
BESKRIVELSE: Skjermkort
SPØRSMÅL: Er kjøpet til privat bruk eller til selskapets drift?"""


class Continuing:
    """A model that records every conversation it is handed and answers the same."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        self.calls.append(
            {"system": system, "prompt": prompt, "history": list(history), "max": max_tokens}
        )
        return self.reply


async def _first_fields() -> tuple[extraction.Extraction, followup.Exchange | None]:
    kept: list[followup.Exchange] = []

    async def stream(*, system: str, prompt: str, max_tokens: int) -> AsyncIterator[str]:
        del system, prompt, max_tokens
        for start in range(0, len(FIRST_ANSWER), 9):
            yield FIRST_ANSWER[start : start + 9]

    async def ignore(_: str) -> None:
        return None

    found = await extraction.extract_streaming(
        RECEIPT, ask=stream, today=TODAY, on_description=ignore, on_exchange=kept.append
    )
    return found, (kept[0] if kept else None)


# --- What the user said, as the model reads it ------------------------------------


def test_every_answer_so_far_goes_in_the_one_new_turn() -> None:
    """Not just the newest: the follow-up is the first exchange plus this turn, nothing else."""
    said = followup.render(
        [
            followup.Answer("Er kjøpet privat eller til drift?", "Selskapets drift", "ki"),
            followup.Answer("Én gjenstand eller flere?", "Flere ting", "driftsmiddel"),
        ],
        note="Kjøpte skjermkort på FINN, la ut privat",
    )
    assert "«Er kjøpet privat eller til drift?» Svar: «Selskapets drift»" in said
    assert "«Én gjenstand eller flere?» Svar: «Flere ting»" in said
    assert said.endswith("«Kjøpte skjermkort på FINN, la ut privat»")


def test_nothing_said_renders_nothing() -> None:
    assert followup.render([]) == ""
    assert followup.render([followup.Answer("Hva?", "   ")]) == ""


def test_an_answer_is_cut_to_what_a_description_may_hold() -> None:
    """A caller cannot fill the model's window by answering at length."""
    said = followup.render([followup.Answer("Hva?", "x" * 5000)])
    assert len(said) < followup.MAX_ANSWER_CHARS + 100


def test_answers_with_nothing_to_continue_become_a_note() -> None:
    folded = followup.folded([followup.Answer("Til drift?", "Ja")], note="Skjermkort")
    assert folded == "Skjermkort Til drift? Ja"
    assert followup.folded([]) is None


# --- The first exchange is kept exactly ------------------------------------------


async def test_the_first_category_question_is_kept_word_for_word() -> None:
    kept: list[followup.Exchange] = []
    model = Continuing("7")
    await classification.classify(
        RECEIPT, ask=model, allowed=CashFlowDirection.UT, on_exchange=kept.append
    )
    assert kept == [
        followup.Exchange(
            system=model.calls[0]["system"], prompt=model.calls[0]["prompt"], reply="7"
        )
    ]


async def test_the_first_fields_question_is_kept_only_when_its_answer_arrived_whole() -> None:
    """A follow-up on half an answer would show the model a reply it never finished."""
    found, exchange = await _first_fields()
    assert exchange is not None
    assert exchange.reply == FIRST_ANSWER
    assert found.question == "Er kjøpet til privat bruk eller til selskapets drift?"

    kept: list[followup.Exchange] = []

    async def dies(*, system: str, prompt: str, max_tokens: int) -> AsyncIterator[str]:
        del system, prompt, max_tokens
        yield FIRST_ANSWER[:30]
        raise ConnectionError("gone")

    async def ignore(_: str) -> None:
        return None

    await extraction.extract_streaming(
        RECEIPT, ask=dies, today=TODAY, on_description=ignore, on_exchange=kept.append
    )
    assert kept == []


# --- The follow-up ---------------------------------------------------------------


async def test_the_follow_up_starts_exactly_where_the_first_question_did() -> None:
    """The prefix is what the model server reuses, so it has to be byte for byte the same."""
    _, exchange = await _first_fields()
    assert exchange is not None
    model = Continuing(FIRST_ANSWER)
    said = followup.render([followup.Answer("Er kjøpet privat?", "Selskapets drift", "ki")])

    await extraction.extract_again(exchange, said, ask=model, document_text=RECEIPT, today=TODAY)

    (call,) = model.calls
    assert call["system"] == exchange.system
    assert call["history"] == [(exchange.prompt, exchange.reply)]
    assert call["prompt"].startswith(said)
    assert "Ikke still et spørsmål brukeren allerede har svart på" in call["prompt"]


async def test_an_answer_cannot_put_a_seller_on_the_form_the_bilag_does_not_print() -> None:
    """The quotes are still checked against the documents, and the answer is not one of them.

    The user wrote a name in an answer and the model repeated it as the seller.
    It is not printed on the receipt, so it is not the seller.
    """
    _, exchange = await _first_fields()
    assert exchange is not None
    model = Continuing(
        "DATO: 17.10.2026\nVALUTA: NOK\nMOTPART: Ola Nordmann\n"
        "BESKRIVELSE: Skjermkort til selskapets drift\nSPØRSMÅL: UKJENT"
    )
    said = followup.render([followup.Answer("Hvem solgte det?", "Ola Nordmann")])

    found = await extraction.extract_again(
        exchange, said, ask=model, document_text=RECEIPT, today=TODAY
    )

    assert found.counterparty_name is None
    assert found.occurred_on == date(2026, 10, 17)
    assert found.description == "Skjermkort til selskapets drift"
    assert found.question is None


async def test_the_category_follow_up_reads_its_numbers_off_the_first_menu() -> None:
    kept: list[followup.Exchange] = []
    first = Continuing("9/20")
    menu = classification.build_menu(CashFlowDirection.UT)
    await classification.classify(
        RECEIPT, ask=first, allowed=CashFlowDirection.UT, on_exchange=kept.append
    )
    chosen = next(item for item in menu if item.type_code == "inventar")
    model = Continuing(str(chosen.number))

    found = await classification.classify_again(
        kept[0], "Brukeren har svart: til drift", menu=menu, ask=model
    )

    assert found.chosen is not None
    assert found.chosen.code == "inventar"
    assert not found.in_doubt
    assert model.calls[0]["history"] == [(kept[0].prompt, kept[0].reply)]


async def test_a_follow_up_off_the_menu_is_no_answer() -> None:
    menu = classification.build_menu(CashFlowDirection.UT)
    exchange = followup.Exchange(system="s", prompt="p", reply="3")
    found = await classification.classify_again(
        exchange, "", menu=menu, ask=Continuing("Det kommer an på")
    )
    assert found.chosen is None
