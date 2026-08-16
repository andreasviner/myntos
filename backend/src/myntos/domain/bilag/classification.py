"""Asking a model which category a bilag belongs to.

:mod:`myntos.domain.bilag.interpretation` reads the things a document states
outright — the date, the total, the VAT, the organisasjonsnummer — and gets them
right because they are printed. What it cannot read is the one thing the
document never says: which category the payment belongs to, and therefore which
account and which post in næringsspesifikasjonen. Keyword matching covers the
obvious cases ("nettleie" is electricity) and shrugs at everything else.

That is the question put to the model here, and only that question. Nothing else
about the proposal is left to it: amounts and dates come from the text itself,
where an arithmetic mistake by a language model would be both invisible and
expensive.

The interface is a **numbered menu**. The categories are listed with a number
each and the model answers with one number, which is the smallest answer a model
can give: there is nothing to parse, nothing to spell, and an answer outside the
range is recognisably wrong rather than subtly wrong. It also means a small,
cheap model is enough — the task is multiple choice, not composition.

Two safeguards, because the model is small and the consequence is a posting:

- The reply must be a number **in range**, or the whole thing is discarded and
  the caller keeps its own reading. Anything else the model might say is not
  interpreted, ever.
- The caller may restrict the menu (see ``allowed``), which is how a category in
  the wrong direction is made impossible rather than merely unlikely.

Which model answers is not this module's business. :func:`classify` is handed an
``ask`` callable — :func:`myntos.integrations.loynd.complete` in the running
application — so the domain layer keeps its rule of importing nothing from
``integrations``, and a test can answer for the model with a plain function.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from myntos.domain.ledger.cashflow import CashFlowDirection, CashFlowType, types_for

logger = logging.getLogger(__name__)


class Ask(Protocol):
    """Something that can put one question to a model and return its answer."""

    def __call__(self, *, system: str, prompt: str, max_tokens: int) -> Awaitable[str]: ...


# How much of the document the model is shown. The top of an invoice carries the
# sender, the item lines and the totals; the rest is payment instructions and
# small print. Sending less is faster, cheaper, and less of the user's business
# leaving the building than sending it all.
MAX_DOCUMENT_CHARS = 2000

_SYSTEM = (
    "Du er en norsk regnskapsassistent. Du får en faktura eller kvittering og en "
    "nummerert liste med kategorier. Du svarer med kun ett tall fra listen, uten "
    "forklaring, uten punktum og uten andre ord."
)
_ANSWER = re.compile(r"\d{1,3}")

_DIRECTION_LABEL = {
    CashFlowDirection.INN: "penger inn",
    CashFlowDirection.UT: "penger ut",
}


@dataclass(frozen=True)
class MenuItem:
    """One numbered choice, as the model sees it and as we read it back."""

    number: int
    type_code: str
    label: str
    direction: CashFlowDirection


def build_menu(allowed: CashFlowDirection | None = None) -> tuple[MenuItem, ...]:
    """The categories on offer, numbered from one.

    Restricting to a direction is not a shortcut: when the counterparty is
    already known to be a registered customer, an expense category is not a
    plausible answer, and a menu that does not contain it cannot be chosen by
    mistake.
    """
    return tuple(
        MenuItem(number=index, type_code=entry.code, label=entry.label, direction=entry.direction)
        for index, entry in enumerate(types_for(allowed), start=1)
    )


def render_menu(menu: tuple[MenuItem, ...]) -> str:
    return "\n".join(
        f"{item.number}. {item.label} ({_DIRECTION_LABEL[item.direction]})" for item in menu
    )


def build_prompt(document_text: str, menu: tuple[MenuItem, ...]) -> str:
    """The question, with the document above it and the menu below.

    The menu comes last on purpose: a small model attends most closely to the end
    of the prompt, and the instruction it must not lose is the one about
    answering with a number.
    """
    excerpt = "\n".join(line for line in document_text.splitlines() if line.strip())
    return (
        "Her er teksten fra et bilag:\n"
        "---\n"
        f"{excerpt[:MAX_DOCUMENT_CHARS]}\n"
        "---\n"
        "Hvilken kategori hører denne betalingen til?\n\n"
        f"{render_menu(menu)}\n\n"
        "Svar med kun tallet til kategorien som passer best."
    )


def parse_choice(reply: str, menu: tuple[MenuItem, ...]) -> MenuItem | None:
    """The chosen item, or ``None`` if the reply was not one of the numbers.

    The first number in the reply is the answer: a model that has been told to
    answer with a number and answers "3" is unambiguous, and one that answers
    "Kategori 3 — strøm" meant the same thing. Everything after it is ignored,
    and a number outside the menu is no answer at all.
    """
    match = _ANSWER.search(reply)
    if match is None:
        return None
    number = int(match.group())
    return next((item for item in menu if item.number == number), None)


async def classify(
    document_text: str, *, ask: Ask, allowed: CashFlowDirection | None = None
) -> CashFlowType | None:
    """Ask which category the document belongs to.

    Returns ``None`` when there is nothing worth asking about, or when the answer
    is not one of the numbers on the menu. Whether the model was reachable at all
    is the caller's problem, since the caller is the one that knows what it
    called: an unavailable model is not an error for the user, it just means the
    register keeps its own reading.
    """
    if not document_text.strip():
        return None

    menu = build_menu(allowed)
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(document_text, menu),
        # Room for a number and nothing else. A model that starts explaining gets
        # cut off, and the first number is what we were after anyway.
        max_tokens=8,
    )
    chosen = parse_choice(reply, menu)
    if chosen is None:
        logger.info("the model answered outside the menu; keeping our own reading")
        return None
    return next(entry for entry in types_for() if entry.code == chosen.type_code)
