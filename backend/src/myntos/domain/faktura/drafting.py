"""Turning a sentence about a sale into invoice lines.

An invoice line says four things: what was sold, how much of it, at what price,
and under which VAT treatment. A person who has just done the work knows all
four, and typing them into five boxes per line is the slowest part of writing an
invoice. This module asks a model to lay that sentence out as lines instead.

It is the same bargain as :mod:`myntos.domain.bilag.classification`, drawn in
the same place: **the model restates what the user already said, and decides
nothing else.** The numbers come from the user's own description — the model is
told not to convert, not to add, and not to total. Everything downstream is
computed by :mod:`myntos.domain.faktura.service` from the quantity and the price
exactly as a hand-typed line would be, so a model that misreads produces a line
the user can see is wrong, never a total that is quietly off.

Three things keep that promise:

**A fixed, parseable answer.** One line per invoice line, five fields separated
by ``|``. Anything that does not parse into five fields is discarded rather than
guessed at, and the user is told how many were dropped.

**A numbered menu for the VAT code**, exactly as in the classification module: a
model cannot invent a treatment that does not exist, and a caller may narrow the
menu — which is how a company outside Merverdiavgiftsregisteret is made unable
to receive a proposal that charges VAT, rather than merely unlikely to.

**Bounds on every number.** A quantity or a price outside what the column holds,
or with more decimals than it holds, is rounded or refused here — before the
service layer would raise :class:`~myntos.domain.faktura.errors.InvalidLineError`
at the user for something the model did.

Nothing here is required. The caller is expected to fall back to an empty form,
because writing an invoice by hand must keep working when no model answers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from myntos.domain.bilag.interpretation import parse_amount
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS, UNITS
from myntos.domain.faktura.models import DEFAULT_UNIT_CODE
from myntos.domain.ledger.chart import TAX_CODES_BY_CODE

logger = logging.getLogger(__name__)


class Ask(Protocol):
    """Something that can put one question to a model and return its answer."""

    def __call__(self, *, system: str, prompt: str, max_tokens: int) -> Awaitable[str]: ...


# How much of the user's description is sent. A sale worth invoicing can be
# described in a paragraph; anything longer is a document, and a document belongs
# in the bilag register rather than in this box.
MAX_REQUEST_CHARS = 800

# The most lines one proposal may produce. A form the user has to prune is worse
# than one they have to extend, and a runaway model is what this guards against.
MAX_LINES = 20

# Room for the lines and nothing else. Roughly thirty tokens a line, plus slack.
_MAX_TOKENS = 640

# What the columns hold (see myntos.domain.faktura.service). Numbers past these
# are refused here rather than at the service layer, where the message would
# blame the user for something the model wrote.
_QUANTITY_DECIMALS = 6
_PRICE_DECIMALS = 4
_MAX_QUANTITY = Decimal(1_000_000)
_MAX_UNIT_PRICE = Decimal(1_000_000_000)

_SYSTEM = (
    "Du er en norsk fakturaassistent. Du får en beskrivelse av et salg og svarer "
    "med én linje per vare eller tjeneste, i formatet "
    "«beskrivelse | antall | enhet | pris | mva». Du skriver ingenting annet: "
    "ingen overskrift, ingen forklaring, ingen sum, ingen mva-beregning."
)

_SEPARATOR = "|"
_FIELDS = 5
# A leading bullet or "1." the model added out of habit. The dot or parenthesis
# is required, so a description that genuinely starts with a number survives.
# The class holds a hyphen, an asterisk, a bullet and an en dash — the four
# things a model puts in front of a list item. RUF001 objects to the last two
# looking alike, which here is the point: both turn up, and both are stripped.
_BULLET = re.compile(r"^\s*(?:[-*•–]\s*|\d{1,2}[.)]\s+)")  # noqa: RUF001
_MENU_NUMBER = re.compile(r"\d{1,3}")
# The model echoing the format line back as its first answer.
_HEADER_WORDS = ("beskrivelse", "vare", "tjeneste", "produkt")

# The unit words a proposal may use, and the UN/ECE code each one means. Built
# from the catalogue's own labels so a new unit there is offered here too, with
# the plural and shorthand forms a person actually writes added on top — the
# model is answering in Norwegian, not in UN/ECE Recommendation 20.
_UNIT_ALIASES: dict[str, str] = {
    "stk": "EA",
    "stykk": "EA",
    "stykker": "EA",
    "enhet": "EA",
    "enheter": "EA",
    "timer": "HUR",
    "t": "HUR",
    "dager": "DAY",
    "døgn": "DAY",
    "uker": "WEE",
    "mnd": "MON",
    "måneder": "MON",
    "år": "ANN",
    "kilo": "KGM",
    "kg": "KGM",
    "liter": "LTR",
    "l": "LTR",
    "m": "MTR",
    "meter": "MTR",
    "m2": "MTK",
    "kvm": "MTK",
    "m3": "MTQ",
    "km": "KMT",
    "par": "PR",
}
_UNIT_BY_WORD: dict[str, str] = {unit.label.lower(): unit.code for unit in UNITS} | _UNIT_ALIASES

# Said out loud rather than acted on: an invoice line is priced without VAT, and
# a description that quotes a gross price would otherwise become a line that is
# 25 % too high without anything on screen saying so.
_GROSS_HINTS = ("inkl. mva", "inkl mva", "inklusive mva", "med mva", "inkludert mva")


@dataclass(frozen=True)
class TaxChoice:
    """One numbered VAT treatment, as the model sees it and as we read it back."""

    number: int
    tax_code: str
    label: str


@dataclass(frozen=True)
class DraftedLine:
    """One proposed invoice line, in the shape the form takes."""

    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    tax_code: str


@dataclass
class LineProposal:
    """What came back: the lines that parsed, and what the user should know.

    ``lines`` is empty whenever nothing usable arrived, which is a normal outcome
    and not an error — ``warnings`` then says why, in a sentence meant to be
    shown next to a form the user can still fill in themselves.
    """

    lines: list[DraftedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_tax_menu(allowed: Sequence[str] | None = None) -> tuple[TaxChoice, ...]:
    """The VAT treatments on offer, numbered from one.

    ``allowed`` restricts the menu to specific tax codes. That is what a company
    outside Merverdiavgiftsregisteret needs: charging output VAT without being
    registered is not a formality but collecting a tax on the state's behalf
    without the right to, so the option is removed rather than merely discouraged.
    Codes that are not in the catalogue at all are ignored, so a caller cannot
    widen the menu by asking for one.
    """
    options = SALES_TAX_OPTIONS
    if allowed is not None:
        wanted = set(allowed)
        options = tuple(option for option in options if option.tax_code in wanted)
        if not options:  # pragma: no cover - a caller asking for nothing valid
            options = SALES_TAX_OPTIONS
    return tuple(
        TaxChoice(number=index, tax_code=option.tax_code, label=option.label)
        for index, option in enumerate(options, start=1)
    )


def render_tax_menu(menu: tuple[TaxChoice, ...]) -> str:
    return "\n".join(f"{choice.number}. {choice.label}" for choice in menu)


def unit_for(word: str) -> str | None:
    """The UN/ECE code a Norwegian unit word means, or ``None`` if unrecognised."""
    cleaned = word.strip().strip(".").lower()
    return _UNIT_BY_WORD.get(cleaned)


def _rate_percent(tax_code: str) -> Decimal:
    """The seeded rate for a tax code, as a percentage."""
    return TAX_CODES_BY_CODE[tax_code].rate * 100


def tax_for(field: str, menu: tuple[TaxChoice, ...]) -> TaxChoice | None:
    """The VAT treatment a field names, by menu number *or* by rate.

    The menu asks for a number, and models mostly answer with one — but a model
    shown "1. 25 % (alminnelig sats)" also quite reasonably answers "25 %", which
    is the rate rather than the choice. Both are read, because refusing the
    second would mean falling back to a default on an answer that was in fact
    unambiguous.

    A percent sign settles it: with one, the number is a rate and nothing else.
    Without one, a valid menu number wins — the menu is what was asked for — and
    only then is the number tried as a rate, which is what makes a bare "25"
    (no menu entry has that number) resolve rather than fall back.

    A rate several treatments share resolves to the first of them on the menu.
    That is the literal reading of "0 %": avgiftsfritt salg, not one of the other
    zero-rated categories that mean something more specific.
    """
    match = _MENU_NUMBER.search(field)
    if match is None:
        return None
    number = int(match.group())
    if "%" not in field:
        chosen = next((item for item in menu if item.number == number), None)
        if chosen is not None:
            return chosen
    return next((item for item in menu if _rate_percent(item.tax_code) == number), None)


def build_prompt(description: str, menu: tuple[TaxChoice, ...]) -> str:
    """The question: the user's own words above, the format and the menu below.

    The instructions come last for the same reason they do in
    :mod:`myntos.domain.bilag.classification` — a small model attends most
    closely to the end of the prompt, and the instruction it must not lose is the
    one about the answer's shape.
    """
    text = "\n".join(line for line in description.splitlines() if line.strip())
    units = ", ".join(unit.label for unit in UNITS)
    return (
        "Slik beskrev selgeren salget:\n"
        "---\n"
        f"{text[:MAX_REQUEST_CHARS]}\n"
        "---\n"
        "Skriv fakturalinjene. Én linje per vare eller tjeneste, i dette formatet:\n\n"
        "beskrivelse | antall | enhet | pris per enhet | mva\n\n"
        f"«enhet» er ett av disse ordene: {units}.\n"
        "«pris per enhet» er prisen uten merverdiavgift, slik den står i beskrivelsen. "
        "Regn ikke om, legg ikke til mva, og skriv ingen sum.\n"
        "«mva» er tallet til satsen som passer:\n"
        f"{render_tax_menu(menu)}\n\n"
        "Svar med kun linjene."
    )


def _rounded(value: Decimal, decimals: int) -> Decimal:
    """``value`` with at most ``decimals`` places, and no trailing zeroes added."""
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -decimals:
        return value.quantize(Decimal(1).scaleb(-decimals))
    return value


def _quantity(raw: str) -> Decimal | None:
    """The quantity, or ``None`` when the field says nothing usable.

    A blank or unreadable quantity is not fatal — :func:`parse_lines` falls back
    to one, which is both the catalogue default and the answer for the great
    majority of lines. A quantity of zero or a negative one *is* fatal, because
    neither is a line anyone meant to write.
    """
    value = parse_amount(raw)
    if value is None or value <= 0 or value > _MAX_QUANTITY:
        return None
    return _rounded(value, _QUANTITY_DECIMALS)


def _unit_price(raw: str) -> Decimal | None:
    value = parse_amount(raw)
    if value is None or value < 0 or value > _MAX_UNIT_PRICE:
        return None
    return _rounded(value, _PRICE_DECIMALS)


def parse_lines(reply: str, menu: tuple[TaxChoice, ...]) -> LineProposal:
    """Read the model's answer into lines, dropping everything that is not one.

    Deliberately unforgiving about shape and forgiving about content. A row with
    the wrong number of fields is discarded — there is no way to tell which field
    is missing, and guessing would put a price in the quantity column. A row with
    a field we cannot read but the right shape keeps its description and loses
    only that field, because the description is the part the user would otherwise
    have to retype.
    """
    proposal = LineProposal()
    if not menu:  # pragma: no cover - build_tax_menu never returns empty
        return proposal

    dropped = 0
    unreadable_price = False
    guessed_tax = False

    for raw in reply.splitlines():
        row = _BULLET.sub("", raw).strip()
        if not row or _SEPARATOR not in row:
            # Blank lines and any prose the model wrapped its answer in. Not
            # counted as dropped: they were never a line to begin with.
            continue
        if len(proposal.lines) >= MAX_LINES:
            dropped += 1
            continue

        parts = [part.strip() for part in row.split(_SEPARATOR)]
        if len(parts) != _FIELDS:
            dropped += 1
            continue

        description, quantity_text, unit_text, price_text, tax_text = parts
        if not description:
            dropped += 1
            continue
        low = description.lower()
        if any(low.startswith(word) for word in _HEADER_WORDS) and not _quantity(quantity_text):
            # The format line echoed back, not a sale.
            continue

        price = _unit_price(price_text)
        if price is None:
            unreadable_price = True

        choice = tax_for(tax_text, menu)
        if choice is None:
            # The first entry is the ordinary case — 25 % for a registered
            # company, "ikke mva-registrert" for one that is not. Saying so is
            # what makes it a proposal rather than a silent decision.
            choice = menu[0]
            guessed_tax = True

        proposal.lines.append(
            DraftedLine(
                description=description[:500],
                quantity=_quantity(quantity_text) or Decimal(1),
                unit_code=unit_for(unit_text) or DEFAULT_UNIT_CODE,
                unit_price=price if price is not None else Decimal(0),
                tax_code=choice.tax_code,
            )
        )

    if dropped:
        proposal.warnings.append(
            f"Vi forsto ikke {dropped} av linjene i forslaget, så de er utelatt. "
            "Legg dem til selv hvis noe mangler."
        )
    if unreadable_price:
        proposal.warnings.append("Vi fant ikke prisen på alle linjene. Fyll inn de som står tomme.")
    if guessed_tax:
        proposal.warnings.append(
            f"Mva-satsen på minst én linje er satt til «{menu[0].label}» fordi forslaget "
            "ikke var tydelig. Kontroller satsen på hver linje."
        )
    return proposal


async def draft_lines(
    description: str, *, ask: Ask, allowed_tax_codes: Sequence[str] | None = None
) -> LineProposal:
    """Propose invoice lines for the sale ``description`` describes.

    Returns an empty proposal — with a sentence saying so — rather than raising
    when the answer holds no usable line. Whether the model was reachable at all
    is the caller's problem: an unavailable model is not an error for someone
    writing an invoice, it just means the lines get typed.
    """
    proposal = LineProposal()
    if not description.strip():
        return proposal

    menu = build_tax_menu(allowed_tax_codes)
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(description, menu),
        max_tokens=_MAX_TOKENS,
    )
    proposal = parse_lines(reply, menu)

    if not proposal.lines:
        logger.info("the model answered with nothing we could read as invoice lines")
        proposal.warnings.insert(
            0,
            "Vi fikk ikke laget fakturalinjer av beskrivelsen. Skriv gjerne hva som ble "
            "solgt, hvor mye og til hvilken pris, eller fyll inn linjene selv.",
        )
        return proposal

    low = description.lower()
    if any(hint in low for hint in _GROSS_HINTS):
        proposal.warnings.append(
            "Beskrivelsen nevner priser inkludert mva. Fakturalinjer prises uten mva, "
            "og merverdiavgiften legges til nederst. Kontroller prisene."
        )
    return proposal
