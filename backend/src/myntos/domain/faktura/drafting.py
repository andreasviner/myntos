"""Turning what a seller pastes or writes into an invoice.

An invoice says who it is to, when and where the sale was delivered, what the
buyer wants it marked with, and then one line per thing sold: what, how much, at
what price, under which VAT treatment. Most of that already exists as text before
anyone opens the form, either as the seller's own sentence about the sale or as
the e-mail in which the customer sent their invoice details. Typing it across
into a dozen boxes is the slowest part of writing an invoice, so this module
asks a model to lay the text out as the form instead.

It is the same bargain as :mod:`myntos.domain.bilag.classification`, drawn in
the same place: **the model restates what the text already says, and decides
nothing else.** The numbers come from the text itself: the model is told not to
convert, not to add, and not to total. Everything downstream is computed by
:mod:`myntos.domain.faktura.service` from the quantity and the price exactly as
a hand-typed line would be, so a model that misreads produces a line the user
can see is wrong, never a total that is quietly off.

Five things keep that promise:

**A fixed, parseable answer.** Six ``felt: verdi`` rows for the buyer and the
terms, then one row per invoice line with five fields separated by ``|``.
Anything that does not parse is discarded rather than guessed at, and the user
is told how many lines were dropped.

**Every word of the buyer and the terms must be in the text.** A name, an
address, a reference or a place the model gives back is kept only when the same
words stand in what the user pasted, in the same order, and a date only when its
day and month do. A model that invents a customer gets an empty field, not a
plausible stranger on a document that can only be undone by kreditnota.

**What a rule can read is not asked of the model.** The organisasjonsnummer is
read out of the text by its mod-11 check digit, and the address to send the
invoice to by its ``@``. The model is not shown the customer register either:
which registered customer the text means is decided by :func:`match_customer`,
here, and said out loud when the name only partly fits.

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
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from myntos.domain.bilag.interpretation import orgnrs_in, parse_amount
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS, UNITS
from myntos.domain.faktura.models import DEFAULT_UNIT_CODE
from myntos.domain.ledger.chart import TAX_CODES_BY_CODE

logger = logging.getLogger(__name__)


class Ask(Protocol):
    """Something that can put one question to a model and return its answer."""

    def __call__(self, *, system: str, prompt: str, max_tokens: int) -> Awaitable[str]: ...


# How much of the user's text is sent. A sale can be described in a paragraph,
# but the customer's own e-mail with their invoice details is what people paste
# here, greeting and signature included, and that runs to a screen. 800 was the
# paragraph; a real one from a customer was 600 before its signature. Anything
# longer is a document, and a document belongs in the bilag register.
MAX_REQUEST_CHARS = 2000

# The most lines one proposal may produce. A form the user has to prune is worse
# than one they have to extend, and a runaway model is what this guards against.
MAX_LINES = 20

# Room for the six rows about the buyer and the lines. Roughly thirty tokens a
# row, plus slack.
_MAX_TOKENS = 800

# What the columns hold (see myntos.domain.faktura.service). Numbers past these
# are refused here rather than at the service layer, where the message would
# blame the user for something the model wrote.
_QUANTITY_DECIMALS = 6
_PRICE_DECIMALS = 4
_MAX_QUANTITY = Decimal(1_000_000)
_MAX_UNIT_PRICE = Decimal(1_000_000_000)

_SYSTEM = (
    "Du er en norsk fakturaassistent. Du får en tekst om en faktura som skal skrives: "
    "selgerens egen beskrivelse av salget, en e-post fra kunden med fakturaopplysninger, "
    "eller begge deler. Du svarer i et fast format og skriver ingenting annet: "
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

# The rows about the buyer and the terms, in the order the prompt asks for them.
# A row is ``felt: verdi`` and never holds a ``|``, but one whose value happens
# to is still a row about the buyer and not a line, so it is recognised first.
_FIELD_ROW = re.compile(
    r"^(kunde|adresse|referanse|leveringsdato|leveringssted|forfall)\s*:\s*(.*)$", re.IGNORECASE
)

# Words, for comparing what the model said with what the text says. Letters and
# digits of any script, so «Sunndalsøra» is one word and «GS-HR00003» two.
_WORD = re.compile(r"[^\W_]+")

# What a company name may end in without that being part of what anyone writes.
# «Bygg» in a sentence and «Bygg AS» in the register are the same customer.
_LEGAL_FORMS = frozenset({"as", "asa", "ans", "da", "nuf", "sa", "ba", "ks", "enk", "sf", "iks"})
_LEGAL_FORM_TAIL = re.compile(
    r"(?:[\s,]+(?:" + "|".join(sorted(_LEGAL_FORMS)) + r")\.?)+\s*$", re.IGNORECASE
)
# An orgnr the model put after the name. The number is read from the text by its
# check digit (orgnrs_in), so here it is only in the way of comparing names.
_ORGNR_TAIL = re.compile(
    r"[,\s]*\b(?:org\.?\s*nr\.?|orgnr|organisasjonsnummer)\b.*$", re.IGNORECASE
)
# A label the model kept in front of a reference: «Faktura merkes: …».
_REFERENCE_LABEL = re.compile(
    r"^(?:faktura\s+merkes(?:\s+med)?|merkes(?:\s+med)?|merking|deres\s+ref(?:eranse)?\.?"
    r"|ref(?:eranse)?\.?)\s*:?\s*",
    re.IGNORECASE,
)
# «Storgata 1, 0155 Oslo»: whatever comes before a four-digit postnummer and
# the place after it.
_POSTAL = re.compile(r"^(?P<line>.*?)[,\s]*(?<!\d)(?P<code>\d{4})\s+(?P<city>[^\d,]+?)[\s.]*$")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DOTTED_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_DAYS = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MONTHS = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)

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


@dataclass(frozen=True)
class DraftedHeader:
    """The buyer and the terms, each only as far as the text says it.

    Named as :class:`~myntos.api.v1.faktura.CreateFakturaRequest` names them, so
    the proposal can go into a draft field for field. ``None`` throughout means
    the text did not say, never that the model did and was overruled silently.

    ``invoice_email`` is where the customer asked for the invoice to be sent.
    Nothing on the invoice holds it, and Myntos sends nothing yet; it is here so
    the person can be told, rather than find it again in the e-mail later.
    """

    buyer_name: str | None = None
    buyer_orgnr: str | None = None
    buyer_address_line: str | None = None
    buyer_postal_code: str | None = None
    buyer_city: str | None = None
    invoice_email: str | None = None
    delivery_date: date | None = None
    delivery_place: str | None = None
    due_date: date | None = None
    buyer_reference: str | None = None

    def is_empty(self) -> bool:
        return self == DraftedHeader()


@dataclass
class InvoiceProposal:
    """What came back: the buyer, the terms, the lines, and what the user should know.

    ``lines`` is empty whenever nothing usable arrived, which is a normal outcome
    and not an error — ``warnings`` then says why, in a sentence meant to be
    shown next to a form the user can still fill in themselves. An e-mail with
    the customer's invoice details and no sale in it is the ordinary case of an
    empty ``lines`` with a full ``header``.

    ``warnings`` are about the lines and ``header_warnings`` about the rest, kept
    apart because the form's second box asks for the lines alone.
    """

    header: DraftedHeader = field(default_factory=DraftedHeader)
    lines: list[DraftedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    header_warnings: list[str] = field(default_factory=list)


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


def sent_text(description: str) -> str:
    """The part of the user's text the model is shown, and so the part it can repeat.

    Blank lines are dropped, because an e-mail pasted from a mail client has one
    after every line, and the length is capped. Everything the answer is checked
    against is this, not the whole paste: a name the model could not have seen
    is a name it made up.
    """
    text = "\n".join(line for line in description.splitlines() if line.strip())
    return text[:MAX_REQUEST_CHARS]


def build_prompt(
    description: str,
    menu: tuple[TaxChoice, ...],
    *,
    today: date,
    seller_name: str | None = None,
) -> str:
    """The question: the user's own text above, the format and the menu below.

    The instructions come last for the same reason they do in
    :mod:`myntos.domain.bilag.classification` — a small model attends most
    closely to the end of the prompt, and the instruction it must not lose is the
    one about the answer's shape.

    Today's date is there for «levert i går», and the seller's name so that the
    company sending the invoice is not mistaken for the one receiving it: in a
    customer's e-mail both are named, and the greeting is to the seller.
    """
    units = ", ".join(unit.label for unit in UNITS)
    seller = f"Selgeren, som skal sende fakturaen, er {seller_name}.\n" if seller_name else ""
    return (
        "Dette er teksten om fakturaen:\n"
        "---\n"
        f"{sent_text(description)}\n"
        "---\n"
        f"{seller}"
        f"I dag er {today.isoformat()}.\n\n"
        "Svar først med disse seks linjene. Skriv det ordrett slik det står i teksten, "
        "og «-» der teksten ikke sier det:\n\n"
        "kunde: navnet på firmaet eller personen som skal betale fakturaen\n"
        "adresse: fakturaadressen på én linje: gate eller postboks, postnummer og sted\n"
        "referanse: det fakturaen skal merkes med, som kundens referanse, "
        "bestillingsnummer eller «Att:»\n"
        "leveringsdato: dagen det ble levert, som ÅÅÅÅ-MM-DD, bare hvis teksten nevner "
        "en bestemt dag\n"
        "leveringssted: stedet det ble levert\n"
        "forfall: antall dager til betaling, eller forfallsdatoen som ÅÅÅÅ-MM-DD\n\n"
        "Skriv så fakturalinjene. Én linje per vare eller tjeneste, i dette formatet:\n\n"
        "beskrivelse | antall | enhet | pris per enhet | mva\n\n"
        f"«enhet» er ett av disse ordene: {units}.\n"
        "«pris per enhet» er prisen uten merverdiavgift, slik den står i teksten. "
        "Regn ikke om, legg ikke til mva, og skriv ingen sum.\n"
        "«mva» er tallet til satsen som passer:\n"
        f"{render_tax_menu(menu)}\n\n"
        "Sier ikke teksten hva som er solgt, skriver du ingen fakturalinjer.\n"
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


def parse_lines(reply: str, menu: tuple[TaxChoice, ...]) -> InvoiceProposal:
    """Read the model's answer into lines, dropping everything that is not one.

    Deliberately unforgiving about shape and forgiving about content. A row with
    the wrong number of fields is discarded — there is no way to tell which field
    is missing, and guessing would put a price in the quantity column. A row with
    a field we cannot read but the right shape keeps its description and loses
    only that field, because the description is the part the user would otherwise
    have to retype.
    """
    proposal = InvoiceProposal()
    if not menu:  # pragma: no cover - build_tax_menu never returns empty
        return proposal

    dropped = 0
    unreadable_price = False
    guessed_tax = False

    for raw in reply.splitlines():
        row = _BULLET.sub("", raw).strip()
        if _FIELD_ROW.match(row):
            # A row about the buyer; parse_header reads those.
            continue
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


# --- The buyer and the terms ------------------------------------------------------


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def said(value: str, text: str) -> bool:
    """Whether every word of ``value`` stands in ``text``, together and in order.

    The test a buyer's name, address or reference has to pass before it reaches
    the form. Case, punctuation and line breaks are ignored, because an e-mail
    puts the address over four lines and the model, asked for one, joins them;
    the words themselves are not, because a different word is a different buyer.
    """
    wanted = _words(value)
    if not wanted:
        return False
    have = _words(text)
    width = len(wanted)
    return any(have[start : start + width] == wanted for start in range(len(have) - width + 1))


def _name_words(name: str) -> list[str]:
    """A name as words, without the legal form it may or may not be written with."""
    words = _words(name)
    while len(words) > 1 and words[-1] in _LEGAL_FORMS:
        words.pop()
    return words


def _value(raw: str) -> str | None:
    """A row's value, or ``None`` for the «-» and the «ukjent» that mean nothing."""
    value = " ".join(raw.split()).strip(" ,;")
    if not any(character.isalnum() for character in value):
        return None
    if value.casefold() in {"ingen", "ukjent", "ikke oppgitt", "ikke nevnt", "n/a", "null"}:
        return None
    return value


def _said_text(raw: str, text: str, *, max_length: int) -> str | None:
    value = _value(raw)
    if value is None or len(value) > max_length or not said(value, text):
        return None
    return value


def _buyer_name(raw: str, text: str, *, seller_name: str | None) -> str | None:
    """The buyer's name as the text has it, or ``None``.

    The model may add the legal form the text left out («Bygg» becomes «Bygg
    AS») or the orgnr after the name; neither makes it a different name, so both
    are taken off before the name is looked for. What is kept is what the text
    says. The seller's own name is never the buyer, however the greeting reads.
    """
    value = _value(raw)
    if value is None:
        return None
    value = _ORGNR_TAIL.sub("", re.sub(r"\(.*?\)", " ", value))
    value = " ".join(value.split()).strip(" ,.;:")
    if not value or len(value) > 255:
        return None
    if not said(value, text):
        value = _LEGAL_FORM_TAIL.sub("", value).strip(" ,.")
        if not value or not said(value, text):
            return None
    if seller_name is not None and _name_words(value) == _name_words(seller_name):
        return None
    return value


def _address(raw: str, text: str) -> tuple[str | None, str | None, str | None]:
    """Address line, postnummer and poststed, each kept only if the text has it."""
    value = _value(raw)
    if value is None:
        return None, None, None
    match = _POSTAL.match(value)
    line, code, city = (
        (match["line"], match["code"], match["city"]) if match else (value, None, None)
    )
    line = (line or "").strip(" ,.") or None
    return (
        line if line is not None and len(line) <= 255 and said(line, text) else None,
        code if code is not None and said(code, text) else None,
        city.strip() if city is not None and len(city) <= 128 and said(city, text) else None,
    )


def _date(raw: str) -> date | None:
    value = raw.strip()
    try:
        if match := _ISO_DATE.search(value):
            return date(int(match[1]), int(match[2]), int(match[3]))
        if match := _DOTTED_DATE.search(value):
            return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None
    return None


def _date_said(day: date, text: str, *, today: date) -> bool:
    """Whether the text names ``day``, so the model only wrote it in ISO.

    «i dag» and «i går» are today and yesterday. Otherwise the day of the month
    must be in the text as a number, and the month as a number or a name, and a
    year if the text gives one. That lets «14. oktober», «14.10» and «14/10»
    through and stops a date the text never mentioned, which a model asked for
    one will otherwise produce from «i mars».
    """
    flat = " ".join(text.casefold().split())
    if day == today and ("i dag" in flat or "idag" in flat):
        return True
    if day == today - timedelta(days=1) and ("i går" in flat or "igår" in flat):
        return True
    words = _words(text)
    numbers = {int(word) for word in words if word.isdigit() and len(word) <= 4}
    if day.day not in numbers:
        return False
    years = {number for number in numbers if 1900 <= number <= 2100}
    if years and day.year not in years:
        return False
    month = _MONTHS[day.month - 1]
    named = any(len(word) >= 3 and month.startswith(word) for word in words)
    return named or day.month in numbers


def _due_date(raw: str, text: str, *, today: date) -> date | None:
    """The due date: a date the text names, or today plus a number of days it names.

    Counted from today because the form counts its own default from today, and
    an invoice is dated the day it is issued.
    """
    value = _value(raw)
    if value is None:
        return None
    if (day := _date(value)) is not None:
        return day if _date_said(day, text, today=today) else None
    match = _DAYS.search(value)
    if match is None or match[1] not in _words(text):
        return None
    return today + timedelta(days=int(match[1]))


def _invoice_email(text: str) -> str | None:
    """The address the text says the invoice goes to, if a line about the invoice has one.

    Only on a line that mentions the invoice: an e-mail's signature has the
    sender's own address, and telling the person to send the invoice there would
    be a guess dressed as a reading.
    """
    for line in text.splitlines():
        low = line.casefold()
        if ("faktura" in low or "invoice" in low) and (match := _EMAIL.search(line)):
            return match.group()
    return None


def parse_header(
    reply: str,
    *,
    text: str,
    today: date,
    seller_orgnr: str | None = None,
    seller_name: str | None = None,
) -> tuple[DraftedHeader, list[str]]:
    """Read the buyer and the terms out of the answer, checking each against ``text``.

    ``text`` is what the model was shown (:func:`sent_text`). Returns the header
    and the sentences the user should read about it.
    """
    rows: dict[str, str] = {}
    for raw in reply.splitlines():
        match = _FIELD_ROW.match(_BULLET.sub("", raw).strip())
        if match is not None:
            # The first answer for a field counts; a model that repeats itself
            # has nothing better to say the second time.
            rows.setdefault(match[1].lower(), match[2])

    warnings: list[str] = []
    orgnrs = [orgnr for orgnr in orgnrs_in(text) if orgnr != seller_orgnr]
    buyer_orgnr = orgnrs[0] if len(orgnrs) == 1 else None
    if len(orgnrs) > 1:
        warnings.append(
            f"Teksten har flere organisasjonsnumre ({', '.join(orgnrs)}), så vi valgte ikke "
            "noe av dem. Skriv inn kjøperens selv."
        )

    address_line, postal_code, city = _address(rows.get("adresse", ""), text)
    delivery = _date(rows.get("leveringsdato", ""))
    reference = _value(rows.get("referanse", ""))
    if reference is not None:
        reference = _REFERENCE_LABEL.sub("", reference)
    header = DraftedHeader(
        buyer_name=_buyer_name(rows.get("kunde", ""), text, seller_name=seller_name),
        buyer_orgnr=buyer_orgnr,
        buyer_address_line=address_line,
        buyer_postal_code=postal_code,
        buyer_city=city,
        invoice_email=_invoice_email(text),
        delivery_date=(
            delivery if delivery is not None and _date_said(delivery, text, today=today) else None
        ),
        delivery_place=_said_text(rows.get("leveringssted", ""), text, max_length=255),
        due_date=_due_date(rows.get("forfall", ""), text, today=today),
        buyer_reference=(
            _said_text(reference, text, max_length=128) if reference is not None else None
        ),
    )
    if header.invoice_email is not None:
        warnings.append(
            f"Teksten sier at fakturaen skal sendes til {header.invoice_email}. Myntos sender "
            "ikke fakturaer selv ennå, så last ned PDF-en og send den dit."
        )
    return header, warnings


# --- Which registered customer ---------------------------------------------------


class Registered(Protocol):
    """What matching needs of a customer: the two things a text can name them by."""

    @property
    def name(self) -> str: ...

    @property
    def orgnr(self) -> str | None: ...


#: How a customer was found: by organisasjonsnummer, by the whole name, or by a
#: part of it that fits that customer and no other.
MatchKind = Literal["orgnr", "navn", "delvis"]

# A partial name shorter than this matches too much: «Ola» is a third of a
# register. Exact names have no floor, because «Ås» written out is still «Ås».
_MIN_PARTIAL = 4


def _names(found: Sequence[Registered]) -> str:
    shown = [f"«{party.name}»" for party in found[:3]]
    return ", ".join(shown) + (" med flere" if len(found) > 3 else "")


def match_customer[R: Registered](
    customers: Sequence[R], *, name: str | None, orgnr: str | None
) -> tuple[R | None, MatchKind | None, list[str]]:
    """The registered customer the text means, how sure that is, and what to say.

    The organisasjonsnummer decides when there is one. An orgnr nobody in the
    register has is a new buyer, whatever the name says, so a name match is then
    only mentioned.

    A name matches when it is the customer's name, give or take case and the
    legal form. It also matches when it is a run of the words in exactly one
    customer's name, «Kari» for Kari Nordmann, because that is how people write
    to each other about a customer. That match is said out loud, since the cost
    of the wrong customer on an issued invoice is a kreditnota. Two candidates
    are never chosen between.

    ``customers`` must be one company's, which the caller's query guarantees;
    this function cannot tell.
    """
    if orgnr is not None:
        found = [party for party in customers if party.orgnr == orgnr]
        if len(found) == 1:
            return found[0], "orgnr", []
        if len(found) > 1:
            return (
                None,
                None,
                [
                    f"Flere kunder har organisasjonsnummer {orgnr}: {_names(found)}. Velg riktig "
                    "kunde i listen."
                ],
            )

    if name is None:
        return None, None, []
    target = _name_words(name)
    exact = [party for party in customers if _name_words(party.name) == target]
    partial = [
        party
        for party in customers
        if party not in exact
        and len("".join(target)) >= _MIN_PARTIAL
        and _contains_run(_name_words(party.name), target)
    ]

    if orgnr is not None:
        # The text has a number this register does not know.
        same_name = [party for party in exact + partial if party.orgnr is None]
        if same_name:
            return (
                None,
                None,
                [
                    f"{_names(same_name)} står i kunderegisteret uten organisasjonsnummer, og "
                    f"teksten har {orgnr}. Velg kunden i listen hvis det er den samme."
                ],
            )
        return None, None, []

    if len(exact) == 1:
        return exact[0], "navn", []
    if len(exact) > 1:
        return (
            None,
            None,
            [f"Flere kunder heter «{name}»: {_names(exact)}. Velg riktig kunde i listen."],
        )
    if len(partial) == 1:
        chosen = partial[0]
        return (
            chosen,
            "delvis",
            [
                f"Kunden er satt til «{chosen.name}», fordi det er den eneste kunden som passer "
                f"til «{name}». Kontroller at det er riktig kunde."
            ],
        )
    if len(partial) > 1:
        return (
            None,
            None,
            [f"Flere kunder passer til «{name}»: {_names(partial)}. Velg riktig kunde i listen."],
        )
    return None, None, []


def _contains_run(words: list[str], run: list[str]) -> bool:
    width = len(run)
    return width > 0 and any(
        words[start : start + width] == run for start in range(len(words) - width + 1)
    )


def differs_from(
    header: DraftedHeader, *, address_line: str | None, postal_code: str | None
) -> str | None:
    """A sentence when the text's address is not the one the customer has on file.

    An invoice to a registered customer prints the register's address, so a
    customer who writes «Fakturaadresse: Postboks 53» would otherwise get the
    invoice at the old one without anyone having read the new one. Only what the
    text gives is compared: a postnummer alone says nothing about the street.
    """
    line_moved = header.buyer_address_line is not None and _words(
        header.buyer_address_line
    ) != _words(address_line or "")
    code_moved = header.buyer_postal_code is not None and header.buyer_postal_code != postal_code
    if not (line_moved or code_moved):
        return None
    place = " ".join(part for part in (header.buyer_postal_code, header.buyer_city) if part)
    shown = ", ".join(part for part in (header.buyer_address_line, place) if part)
    return (
        f"Teksten har en annen fakturaadresse enn kunden har i registeret: {shown}. "
        "Fakturaen får adressen i registeret, så endre kunden under Kunder og leverandører "
        "hvis adressen er ny."
    )


# --- Asking ------------------------------------------------------------------------


async def draft_invoice(
    description: str,
    *,
    ask: Ask,
    today: date,
    allowed_tax_codes: Sequence[str] | None = None,
    seller_orgnr: str | None = None,
    seller_name: str | None = None,
) -> InvoiceProposal:
    """Propose the buyer, the terms and the lines for the invoice ``description`` is about.

    Returns an empty proposal — with a sentence saying so — rather than raising
    when the answer holds nothing usable. Whether the model was reachable at all
    is the caller's problem: an unavailable model is not an error for someone
    writing an invoice, it just means the form gets typed.
    """
    proposal = InvoiceProposal()
    if not description.strip():
        return proposal

    menu = build_tax_menu(allowed_tax_codes)
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(description, menu, today=today, seller_name=seller_name),
        max_tokens=_MAX_TOKENS,
    )
    text = sent_text(description)
    proposal = parse_lines(reply, menu)
    proposal.header, proposal.header_warnings = parse_header(
        reply, text=text, today=today, seller_orgnr=seller_orgnr, seller_name=seller_name
    )

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
