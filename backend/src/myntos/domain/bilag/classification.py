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

It may answer with **two** numbers, and that is the one thing it is allowed to
say beyond its answer: two numbers mean it was torn between them. This exists
because of a battery. An invoice whose item line reads "Batteri" came back as
"Drivstoff og bilkostnad" — a car battery is a perfectly good reading of that
word, and the model had no way to say so and no way to say it was guessing. A
single slot gets filled, and a filled slot looks like certainty. Given a second
slot the same document yields "varekjøp, or possibly bilkostnad", which
:mod:`myntos.domain.bilag.questions` turns into a question for the one person
who knows: the one who bought it. The runner-up is read under exactly the same
rule as the answer, so it costs nothing when the model is not in doubt.

Two safeguards, because the model is small and the consequence is a posting:

- The reply must be a number **in range**, or the whole thing is discarded and
  the caller keeps its own reading. Anything else the model might say is not
  interpreted, ever — including the second number, which is dropped if it is not
  on the menu without taking the first one down with it.
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

from myntos.domain.bilag.context import NO_CONTEXT, BilagContext
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
    "nummerert liste med kategorier. Du svarer med ett tall fra listen. Er du "
    "i reell tvil mellom to kategorier, svarer du med begge tallene skilt med "
    "skråstrek, det mest sannsynlige først. Ingen forklaring, ingen punktum og "
    "ingen andre ord."
)
_ANSWER = re.compile(r"\d{1,3}")

#: The most numbers we read out of one reply: the answer, and the runner-up the
#: model names when it is torn. A third would be a shrug, not a doubt.
_MAX_CHOICES = 2

_DIRECTION_LABEL = {
    CashFlowDirection.INN: "penger inn",
    CashFlowDirection.UT: "penger ut",
}


@dataclass(frozen=True)
class Classification:
    """The category the model chose, and the one it nearly chose instead.

    ``alternative`` is only ever set when the model volunteered a second number
    — it is not our own second guess, and there is no scoring behind it. What it
    is for is a question: :func:`myntos.domain.bilag.questions.compose` turns
    "varekjøp, or possibly bilkostnad" into "Er dette «Varekjøp» eller
    «Drivstoff og bilkostnad»?", which is the difference between a register that
    quietly files a battery under motoring and one that asks.
    """

    chosen: CashFlowType | None = None
    alternative: CashFlowType | None = None

    @property
    def in_doubt(self) -> bool:
        """Whether the model named two different categories."""
        return (
            self.chosen is not None
            and self.alternative is not None
            and self.alternative.code != self.chosen.code
        )


@dataclass(frozen=True)
class MenuItem:
    """One numbered choice, as the model sees it and as we read it back."""

    number: int
    type_code: str
    label: str
    direction: CashFlowDirection


def build_menu(
    allowed: CashFlowDirection | None = None, *, returning: bool = False
) -> tuple[MenuItem, ...]:
    """The categories on offer, numbered from one.

    Restricting to a direction is not a shortcut: when the counterparty is
    already known to be a registered customer, an expense category is not a
    plausible answer, and a menu that does not contain it cannot be chosen by
    mistake.

    ``returning`` narrows it once more, to the sixteen refunds, and it is set
    when the bilag shows the money leaving *and* coming back for the same
    counterparty. That is not a guess about what the payment was: both legs are
    printed on the page. Left on the full money-in menu the model answered "Salg
    med 25 % mva" for an eBay refund, which reports output VAT on a purchase
    that was cancelled. Narrowed, the question it is being asked is the one
    worth asking: a refund of *what*.

    **Money in only, and the mirror does not hold.** It is tempting to narrow
    the money-out leg of the same bilag to the six kreditnotaer, since those are
    that direction's reversals, and it was written that way first. It is wrong,
    and the eBay bilag says why: both legs are the same GBP 2 137,00, the
    register books the 1 584,41 the exchange rate cost, and *that* went out. On
    a menu of nothing but kreditnotaer the model answered "Kreditnota: annen
    inntekt", which files a currency loss as revenue given back and hides it
    from the finanskostnader where it belongs. A netted bilag that comes out
    money-out is a currency difference or a partly refunded purchase at least as
    often as it is a customer being paid back, so the answer has to stay
    reachable. Money in with a matching outgoing leg cannot be a sale; money out
    with a matching incoming leg can be almost anything.
    """
    entries = types_for(allowed)
    if returning and allowed is CashFlowDirection.INN:
        entries = [entry for entry in entries if entry.is_refund] or entries
    return tuple(
        MenuItem(number=index, type_code=entry.code, label=entry.label, direction=entry.direction)
        for index, entry in enumerate(entries, start=1)
    )


def render_menu(menu: tuple[MenuItem, ...]) -> str:
    """The numbered choices.

    The direction is printed per line only when the menu actually holds both.
    On a menu narrowed to one direction it is the same three words eighteen
    times, and repetition that carries no information is not free: measured
    against loynd-lite, dropping it and stating the direction once in the
    question moved an eBay receipt for a Mac Studio from "Uttak til eier" to
    "Varekjøp", which is the difference between a nonsense answer and a
    defensible one.
    """
    directions = {item.direction for item in menu}
    if len(directions) < 2:
        return "\n".join(f"{item.number}. {item.label}" for item in menu)
    return "\n".join(
        f"{item.number}. {item.label} ({_DIRECTION_LABEL[item.direction]})" for item in menu
    )


def excerpt(document_text: str, *, title: str | None = None) -> str:
    """The part of a document a model is shown, blank lines dropped.

    Shared with :mod:`myntos.domain.bilag.extraction`, so that both questions
    are asked about exactly the same text and the limit stated on the /ki page
    stays one number rather than two that have to be kept in step.

    ``title`` is the file's own PDF title, put on a labelled line above the
    body. It earns its place because of what it often is: an eBay order
    confirmation says "eBay" in its title and nowhere in its text, an Amazon one
    the same, and without it the model is being asked which category a purchase
    belongs to while the name of the shop has been withheld from it. It is
    labelled rather than pasted in so that the model can tell the difference
    between what the page prints and what the file is called.

    The company context is deliberately **not** part of this. What this function
    returns is the document, and the document is what every answer is checked
    against — fold the user's own note in here and a model that repeats it back
    would have its answer accepted as something the bilag said. See
    :func:`preamble`.
    """
    kept = "\n".join(line for line in document_text.splitlines() if line.strip())
    if title is not None:
        kept = f"Dokumentets tittel: {title}\n{kept}"
    return kept[:MAX_DOCUMENT_CHARS]


def preamble(context: BilagContext | None) -> str:
    """The company context, as a labelled block to put above the document.

    Separate from :func:`excerpt` and separate in the prompt, under a heading
    that says what it is. Both questions use this, so the two prompts state the
    same facts about the company in the same words — a model told the business
    sells software in one question and nothing at all in the other would answer
    them from different premises.

    Returns ``""`` when there is nothing to say, and every caller concatenates
    unconditionally: an absent context then reproduces the prompt this system
    sent before the context existed, byte for byte.
    """
    if context is None or context.is_empty:
        return ""
    return f"Om selskapet som har mottatt bilaget:\n{context.render()}\n\n"


def build_prompt(
    document_text: str,
    menu: tuple[MenuItem, ...],
    *,
    title: str | None = None,
    context: BilagContext | None = None,
) -> str:
    """The question, with the document above it and the menu below.

    The menu comes last on purpose: a small model attends most closely to the end
    of the prompt, and the instruction it must not lose is the one about
    answering with a number.

    When the menu is narrowed to one direction, the question says which one and
    asks about *what was bought* rather than about "denne betalingen". Both
    changes are there because the model needs to be told whose side of the
    transaction it is standing on. Left to work it out from an English-language
    receipt full of the word "Order", it reads a purchase as a sale.
    """
    directions = {item.direction for item in menu}
    if directions == {CashFlowDirection.UT}:
        question = (
            "Dette er et kjøp selskapet har gjort. Hvilken kostnadskategori hører kjøpet til?"
        )
        instruction = "Svar med tallet til kategorien som passer best til det som ble kjøpt."
    elif directions == {CashFlowDirection.INN}:
        question = "Dette er en innbetaling til selskapet. Hvilken kategori hører den til?"
        instruction = "Svar med tallet til kategorien som passer best."
    else:
        question = "Hvilken kategori hører denne betalingen til?"
        instruction = "Svar med tallet til kategorien som passer best."
    # Doubt is worth asking for, and it has to be asked for in the same breath
    # as the answer: a model given one slot fills it and looks certain. Told it
    # may name a second, it names one on the bilag that says "Batteri" and one
    # number on the bilag that says "Nettleie mai" — and the first of those is a
    # question worth putting to the person who bought the thing.
    instruction += (
        " Er du i tvil mellom to kategorier, skriv begge tallene med skråstrek mellom, som 9/20."
    )
    return (
        # The company first, then the document, then the question. A model reads
        # the bilag already knowing whose it is, which is the whole point: a
        # graphics card is varekjøp at a company that builds computers and a
        # privatuttak at a hairdresser's, and the receipt says neither.
        f"{preamble(context)}"
        "Her er teksten fra et bilag:\n"
        "---\n"
        f"{excerpt(document_text, title=title)}\n"
        "---\n"
        f"{question}\n\n"
        f"{render_menu(menu)}\n\n"
        f"{instruction}"
    )


def parse_choices(reply: str, menu: tuple[MenuItem, ...]) -> tuple[MenuItem, ...]:
    """The items the reply named, in the order it named them, at most two.

    The second one is the model saying it was torn, and it is read under the
    same rule as the first: a number that is not on the menu is not an answer,
    so a reply of "9/47" names one category and a reply of "9/9" names one
    category twice. There is nothing to interpret and nothing to spell.
    """
    seen: list[MenuItem] = []
    for match in _ANSWER.finditer(reply):
        chosen = next((item for item in menu if item.number == int(match.group())), None)
        if chosen is not None and chosen not in seen:
            seen.append(chosen)
        if len(seen) == _MAX_CHOICES:
            break
    return tuple(seen)


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
    document_text: str,
    *,
    ask: Ask,
    allowed: CashFlowDirection | None = None,
    title: str | None = None,
    context: BilagContext | None = None,
    returning: bool = False,
) -> Classification:
    """Ask which category the document belongs to.

    Returns an empty :class:`Classification` when there is nothing worth asking
    about, or when the answer is not one of the numbers on the menu. Whether the
    model was reachable at all is the caller's problem, since the caller is the
    one that knows what it called: an unavailable model is not an error for the
    user, it just means the register keeps its own reading.
    """
    context = context or NO_CONTEXT
    # A note the user typed is a reason to ask even about a document we could not
    # read: "kjøpt strømforsyning, la ut privat" is enough to pick a category
    # from, and refusing to ask would waste the one thing the user did supply.
    if not document_text.strip() and title is None and context.note is None:
        return Classification()

    menu = build_menu(allowed, returning=returning)
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(document_text, menu, title=title, context=context),
        # Room for two numbers and the slash between them, and nothing else. A
        # model that starts explaining gets cut off, and the numbers are what we
        # were after anyway.
        max_tokens=12,
    )
    picked = parse_choices(reply, menu)
    if not picked:
        logger.info("the model answered outside the menu; keeping our own reading")
        return Classification()
    return Classification(
        chosen=_type_of(picked[0]),
        alternative=_type_of(picked[1]) if len(picked) > 1 else None,
    )


def _type_of(item: MenuItem) -> CashFlowType:
    """The full catalogue entry behind a menu line."""
    return next(entry for entry in types_for() if entry.code == item.type_code)
