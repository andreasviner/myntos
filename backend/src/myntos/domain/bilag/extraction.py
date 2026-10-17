"""Asking the model to point at the things a keyword list cannot find.

:mod:`myntos.domain.bilag.interpretation` reads what a Norwegian invoice
prints, and does it well, because Norwegian invoices print the same words.
A receipt from a foreign webshop prints none of them: no ``Fakturadato``, no
``Å betale``, no organisasjonsnummer, and a date written ``Aug 23, 2026``
between two headings. The parser is left with a total and nothing to call it.

This is the second question put to Løynd, next to the category
(:mod:`myntos.domain.bilag.classification`), and it is asked the same way: a
tiny answer with a fixed shape, checked against the document before any of it
is believed.

**The model points, the parser still reads.** For the date and the seller the
model is asked to quote the document *verbatim*, and an answer that is not
found in the text is discarded whole. The date it quotes is then parsed by
:func:`myntos.domain.bilag.interpretation.dates_in`, the same function that
reads every other date in the system. So the model decides *which* line on the
page is the date; it never decides what the date is. That distinction is the
whole reason it is allowed near a bilag: a language model that reads a number
wrong reads it wrong in a way that looks right.

**The description is the one field it composes**, because there is nothing to
quote: "hva gjaldt det" is a sentence the document does not contain. It is also
the field where a mistake costs the least — it is prose in the ledger, the user
sees it in the form before posting, and no amount, account or avgift follows
from it. What it must not be is the other fields over again: an invoice for a
battery came back described as "Faktura 1075, LINJEFORENINGEN ELEKTRA", which is
a document type, a number and the sender, each of which already has a field on
the form. :func:`_description` refuses that, and refusing costs nothing, because
the parser's reading of the item line is the fallback and it names the thing.

Everything is optional, exactly as in :mod:`~myntos.domain.bilag.classification`:
an unreachable model, an unparseable answer and an answer contradicted by the
document all end the same way, with the register keeping its own reading.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from myntos.domain.bilag import questions
from myntos.domain.bilag.classification import (
    MAX_BILAG_CHARS,
    Ask,
    excerpt,
    preamble,
)
from myntos.domain.bilag.context import NO_CONTEXT, BilagContext
from myntos.domain.bilag.followup import AskAfter, Exchange
from myntos.domain.bilag.interpretation import (
    NOT_A_PARTY,
    FieldSource,
    InvoiceReading,
    InvoiceSuggestion,
    dates_in,
)

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 120
MAX_NAME_CHARS = 120

# Enough for five short lines and not enough for an essay. A model that starts
# explaining itself gets cut off mid-sentence, and a truncated line fails its
# own check rather than arriving as a half-truth — a question cut off before its
# question mark is refused by `questions.clean` for exactly that reason.
MAX_ANSWER_TOKENS = 220

_SYSTEM = (
    "Du er en norsk regnskapsassistent. Du får teksten fra et bilag og svarer med "
    "nøyaktig fem linjer på formen NØKKEL: verdi. Du finner ikke på noe: står det "
    "ikke i teksten, svarer du UKJENT. Ingen forklaring og ingen andre linjer."
)

# MOTPART says where the seller is printed and where the buyer is, because "the
# seller" alone was not enough on a pro forma invoice from MA-3D: two names on
# the page, the buyer's in the filename too, and nothing telling the model that
# the name over a Trondheim address is the one paying.
#
# BESKRIVELSE says what to do with several orders because the model, shown one
# card charge and nine AliExpress orders, described the first item it read as if
# it were the purchase. The example is deliberately not from any fixture: an
# example names the shape of the answer, and one that named a real bilag's
# items would teach the test its own answer.
_QUESTION = (
    "Svar med nøyaktig disse fem linjene, i denne rekkefølgen:\n"
    "DATO: datoen kjøpet eller fakturaen er datert, avskrevet nøyaktig slik den står "
    "i teksten over\n"
    "VALUTA: valutakoden beløpene er i, tre store bokstaver, for eksempel NOK, GBP eller EUR\n"
    "MOTPART: navnet på selgeren, altså den som har utstedt bilaget og skal ha pengene, "
    "avskrevet nøyaktig slik det står i teksten over. Selgeren står i brevhodet, ved "
    "organisasjonsnummer eller VAT ID, etter «Selger» eller «Sold by», eller som "
    "mottaker av betalingen («Beneficiary», «Betalingsmottaker»). Ikke kjøperen: navnet "
    "over faktura- eller leveringsadressen, etter «Kunde», «Bill to» eller «Customer», er "
    "den som betaler. Dokumentets tittel kan være filnavnet, som den som lagret fila har "
    "valgt, så et navn der er ikke selgeren bare fordi det står der\n"
    "BESKRIVELSE: én kort setning på norsk om hva selskapet fikk for pengene, altså "
    "hvilken vare eller tjeneste det var. Er det flere dokumenter med ulike ordrer eller "
    "varer, beskriv alt samlet, for eksempel «Kontorrekvisita: skrivere, kabler og "
    "en kontorstol», og ikke bare den første varen. Ikke skriv "
    "selgerens navn, fakturanummeret eller "
    f"beløpet: de har egne felt, og en beskrivelse som gjentar dem sier ingenting. Maks "
    f"{MAX_DESCRIPTION_CHARS} tegn\n"
    "SPØRSMÅL: ett kort spørsmål til brukeren om noe bare brukeren kan vite, og "
    "som dokumentene ikke svarer på, for eksempel hvem som eide pengene, hva noe "
    "skal brukes til, eller hva en vare egentlig er når varelinjen bare gir ett ord "
    "som kan bety flere ting («Batteri», «Del», «Diverse»). Ikke spør om beløp, "
    "dato, konto eller mva-sats: det finner vi selv. Skriv UKJENT hvis dokumentene "
    "sier alt som trengs.\n\n"
    "Skriv UKJENT der teksten ikke sier det. Ikke gjett, og ikke skriv noe annet."
)

# The keys, and only the keys. A line the model invents is not read.
_LINE = re.compile(
    r"(?im)^[\s*\-]*(DATO|VALUTA|MOTPART|BESKRIVELSE|SPØRSMÅL|SPORSMAL)\s*[:\-]\s*(.+?)\s*$"
)
# Ways of saying nothing, in both languages the model might answer in.
_NOTHING = frozenset(
    {"", "-", "--", "ukjent", "ukjent.", "unknown", "n/a", "ingen", "none", "null"}
)
_CODE = re.compile(r"(?i)^[a-z]{3}$")
_LETTER = re.compile(r"[A-Za-zÆØÅæøå]")
_QUOTES = "\"'«»`“”‚’ "  # noqa: RUF001

# The currencies a Norwegian business plausibly holds a bilag in. A code outside
# this list is a misread three-letter word ("NEW", "SSD", "GPU") far more often
# than it is a currency, and the consequence of believing one is a total that
# quietly stops being kroner.
KNOWN_CURRENCIES = frozenset(
    {
        "NOK",
        "SEK",
        "DKK",
        "EUR",
        "USD",
        "GBP",
        "CHF",
        "PLN",
        "CZK",
        "ISK",
        "JPY",
        "CNY",
        "AUD",
        "CAD",
        "NZD",
        "SGD",
        "HKD",
        "INR",
        "AED",
    }
)
_KRONER = re.compile(r"(?i)\bnok\b|\bkr\b|kroner")


@dataclass(frozen=True)
class Extraction:
    """What the model says the document says. Every field already checked."""

    occurred_on: date | None = None
    # The code as printed. "NOK" is a real answer here, and means kroner — which
    # is why merging it into an :class:`InvoiceReading` turns it back into None.
    currency_code: str | None = None
    counterparty_name: str | None = None
    description: str | None = None
    # The one thing the documents did not say, asked back. It fills no field and
    # is checked only for shape — see myntos.domain.bilag.questions.clean — but
    # a question is the one kind of answer that cannot be wrong in a way that
    # costs anything, because nothing follows from it until a person replies.
    question: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.occurred_on, self.currency_code, self.counterparty_name, self.description)
        )


def build_prompt(
    document_text: str,
    *,
    title: str | None = None,
    context: BilagContext | None = None,
) -> str:
    """The company, then the document, then the five things we want out of it.

    The context helps most on the field it cannot corrupt. DATO, VALUTA and
    MOTPART are quotes, thrown out unless the document contains them, so nothing
    the preamble says can put a value in any of the three. BESKRIVELSE is the
    composed one, and it is exactly where knowing the business pays: "Kjøp av
    strømforsyning" beats "Betaling til FINN" and neither is written anywhere on
    the page.
    """
    body = excerpt(document_text, title=title)
    return f"{preamble(context)}Her er teksten fra et bilag:\n---\n{body}\n---\n{_QUESTION}"


def _tidy(value: str) -> str:
    """One line of an answer, stripped of quoting and of runs of whitespace."""
    return " ".join(value.split()).strip(_QUOTES)


#: The per-document headings the API writes above each file when a bilag has
#: several (see ``_joined`` in myntos.api.v1.cashflow). They are ours and not the
#: bilag's, so they are taken out before a quote is checked — otherwise a model
#: with nothing better to say answers "MOTPART: Dokument 1: IMG_9362.png" and the
#: check waves it through, because we did write that.
_OUR_HEADING = re.compile(r"(?m)^-{3}\s*Dokument\s+\d+:.*?-{3}\s*$")


def _said(value: str, document_text: str) -> bool:
    """Whether ``value`` is actually written in the document.

    Compared with whitespace collapsed and case folded, because a PDF text layer
    breaks lines where the layout did and a model quotes what it read rather than
    the bytes it read. Everything else about the comparison is literal: this is
    the check that turns "quote the document" from an instruction into a rule.

    The rule only means anything if the haystack is the document. Anything this
    system wrote into the text itself — the file headings above — is removed
    first, for the same reason the user's own note was never put in.
    """
    body = _OUR_HEADING.sub(" ", document_text)
    # The whole bilag's allowance, not one document's: a quote from the fifth
    # file is as much a quote as one from the first, now the model is shown it.
    haystack = " ".join(body[: MAX_BILAG_CHARS * 2].split()).casefold()
    return " ".join(value.split()).casefold() in haystack


def _date(value: str, *, document_text: str, today: date) -> date | None:
    """The date the model pointed at, read by our own parser."""
    if not _said(value, document_text):
        logger.info("the model quoted a date the document does not contain; ignoring it")
        return None
    found = dates_in(value, today=today)
    # More than one date in a quoted date is not a date, it is a line.
    return found[0] if len(found) == 1 else None


def _currency(value: str, document_text: str) -> str | None:
    """The currency code, if it is one and if the document says it too.

    A foreign code is refused outright when the bilag also prints kroner, which
    is the rule :func:`myntos.domain.bilag.interpretation._currency` already
    applies to its own reading — the model is held to it so that it cannot
    conclude what the parser has ruled out. The case that earned it is a bilag
    of three screenshots where one is a Norwegian bank line and another is the
    eBay order it paid for: "GBP" is genuinely written there, the total is
    genuinely kroner, and accepting the quote multiplied a 27 850-krone refund
    by the exchange rate into 353 295.
    """
    code = value.upper()
    if _CODE.match(code) is None or code not in KNOWN_CURRENCIES:
        return None
    if code == "NOK":
        return "NOK" if _KRONER.search(document_text) is not None else None
    if _KRONER.search(document_text) is not None:
        return None
    return code if re.search(rf"(?i)\b{code}\b", document_text) is not None else None


#: What Løynd's extractor writes above each division of a document: "[Page 1]",
#: "[Slide 4]", "[Sheet: Q3]". They are part of the text the model is shown, and
#: therefore part of the text a quote is checked against — so a model with
#: nothing better to offer can name one as the seller and have it accepted.
_SECTION_LABEL = re.compile(r"^\[[^\]]*\]$")


#: Longest a counterparty's name may be, in words.
#:
#: Six covers "Linjeforeningen Elektra", "Brønnøysundregistrene" and
#: "FEARLESS PC Accessories Store" with room to spare, and refuses "en valgte
#: deg. Vi oppdaterer informasjonen her når" — a clause the model lifted out of
#: a FINN status page, which is genuinely written on it and therefore passed the
#: quote check with nothing else to stop it.
MAX_NAME_WORDS = 6

#: Words that are a field label rather than a party. Same idea as
#: ``interpretation._LABEL_WORDS`` and a much shorter list, because here the
#: model has picked one word off a page rather than a parser splitting a line.
#: Kept in :mod:`myntos.domain.bilag.interpretation` so that one list filters
#: both the model's answers and the parser's. See ``NOT_A_PARTY`` there.
_NOT_A_PARTY = NOT_A_PARTY


def _name(value: str, document_text: str) -> str | None:
    """The seller's name, if the document calls them that."""
    if not 2 <= len(value) <= MAX_NAME_CHARS or len(_LETTER.findall(value)) < 2:
        return None
    if _SECTION_LABEL.match(value) is not None:
        return None
    if value.casefold().strip(" .:,-") in _NOT_A_PARTY:
        return None
    # A sentence is not a name. The quote check can only say the words are on
    # the page; it cannot say they are the seller, and on a page of prose that
    # is the whole difficulty.
    if len(value.split()) > MAX_NAME_WORDS:
        return None
    # Lowercase *and* more than one word is a sentence fragment: "en har sendt
    # pakken", the tail of "Selgeren har sendt pakken" on a FINN status page —
    # correctly transcribed, genuinely on the page, and not a party.
    #
    # The word count is what makes the rule safe. A lone lowercase token is a
    # marketplace handle, and handles are most of the counterparties this
    # system meets: "daisyaus-0" on eBay, "solon" and "NikoVisioN" on FINN.
    # Refusing those to catch a clause would trade the common case for the rare.
    if value[:1].islower() and len(value.split()) > 1:
        return None
    return value if _said(value, document_text) else None


#: Words that name a document rather than a purchase. A "description" made of
#: nothing but these and a number is the reference over again, and the reference
#: is already the first half of the line in the ledger.
_ONLY_A_REFERENCE = frozenset(
    {
        "faktura",
        "fakturanr",
        "fakturanummer",
        "kreditnota",
        "kvittering",
        "regning",
        "ordre",
        "ordrenr",
        "ordrenummer",
        "betaling",
        "innbetaling",
        "utbetaling",
        "invoice",
        "receipt",
        "order",
        "payment",
        "nr",
        "no",
        "kr",
        "nok",
    }
)
_WORDS = re.compile(r"[^0-9a-zæøåA-ZÆØÅ]+")


def _words(value: str) -> list[str]:
    """The words in ``value``, lowercased, punctuation dropped."""
    return [word for word in _WORDS.split(value.casefold()) if word]


def _description(value: str, *, counterparty: str | None = None) -> str | None:
    """The sentence, trimmed — or nothing, if it does not say what was bought.

    There is nothing to check this field *against*, since the document does not
    contain the sentence; what can be checked is that it is not one of the two
    things a model reaches for when it has nothing to say. Both were observed on
    one invoice for a battery, which came back as "Faktura 1075, LINJEFORENINGEN
    ELEKTRA" — a document type, a number, and the sender. Every one of those has
    a field of its own on the form, and a description that repeats them answers
    "hva gjaldt det" with silence.

    Refused rather than patched. The description is the one field where nothing
    downstream breaks when it is missing: the parser's own reading of the item
    line takes over, and that names the thing.
    """
    if len(value) < 3 or _LETTER.search(value) is None:
        return None
    words = _words(value)
    if not words:
        return None
    # The seller's name and nothing else. Compared as words so that
    # "LINJEFORENINGEN ELEKTRA" and "Linjeforeningen Elektra." are the same
    # answer, and so that "Batteri fra Linjeforeningen Elektra" — which does say
    # what was bought — survives.
    if counterparty is not None and words == _words(counterparty):
        return None
    if all(word in _ONLY_A_REFERENCE or word.isdigit() for word in words):
        return None
    return value[:MAX_DESCRIPTION_CHARS].strip()


def parse_answer(reply: str, *, document_text: str, today: date) -> Extraction:
    """Read the model's five lines, keeping only what the document backs up."""
    values: dict[str, str] = {}
    for match in _LINE.finditer(reply):
        key = match.group(1).upper()
        value = _tidy(match.group(2))
        if value.casefold() not in _NOTHING and key not in values:
            values[key] = value

    raw_date = values.get("DATO")
    raw_name = values.get("MOTPART")
    raw_description = values.get("BESKRIVELSE")
    raw_currency = values.get("VALUTA")
    raw_question = values.get("SPØRSMÅL") or values.get("SPORSMAL")
    return Extraction(
        occurred_on=(
            _date(raw_date, document_text=document_text, today=today)
            if raw_date is not None
            else None
        ),
        currency_code=_currency(raw_currency, document_text) if raw_currency is not None else None,
        counterparty_name=_name(raw_name, document_text) if raw_name is not None else None,
        description=(
            _description(raw_description, counterparty=raw_name)
            if raw_description is not None
            else None
        ),
        question=questions.clean(raw_question) if raw_question is not None else None,
    )


#: The label, as it appears at the start of its own line. Matched on the answer
#: as it accumulates, so it has to tolerate arriving in pieces — which it does
#: for free, since a partial label simply does not match yet.
_DESCRIPTION_LABEL = re.compile(r"(?im)^[\s*\-]*BESKRIVELSE\s*[:\-]\s*")


class DescriptionStream:
    """Pulls the description out of the answer while it is still being written.

    The model is asked for five labelled lines and the description is the
    fourth, so a caller that wants to show it being typed cannot simply forward
    every token: the first three lines are a date, a currency code and a name,
    each of which has a field of its own, and none of which is prose anybody
    wants to watch appear. This keeps the whole answer for
    :func:`parse_answer` to read afterwards, and hands out only the part of the
    description that is new.

    Nothing here decides anything. The value the form ends up with is still the
    one :func:`apply_description` computes from the finished answer, and it can
    differ from what was streamed — the item line printed on the document beats
    a composed sentence, and the reference is put in front of it. Streaming is
    for the wait, not for the answer.
    """

    def __init__(self) -> None:
        self._answer = ""
        self._shown = ""
        self._closed = False

    @property
    def answer(self) -> str:
        """Everything received so far, exactly as it arrived."""
        return self._answer

    @property
    def shown(self) -> str:
        """The description as far as it has been handed out."""
        return self._shown

    def feed(self, piece: str) -> str:
        """Take one delta, and return whatever of the description is new.

        Returns ``""`` for every token that is not part of the description,
        which is most of them at the start and all of them once the line ends.
        """
        self._answer += piece
        if self._closed:
            return ""
        match = _DESCRIPTION_LABEL.search(self._answer)
        if match is None:
            return ""
        rest = self._answer[match.end() :]
        line, newline, _ = rest.partition("\n")
        if newline:
            # The line is finished; anything after it belongs to SPØRSMÅL.
            self._closed = True
        # Only the leading edge is cleaned. Trimming the trailing edge of a line
        # that is still being written would emit a character and take it back
        # again on the next token, which on screen is a flicker.
        text = line.lstrip(_QUOTES)[:MAX_DESCRIPTION_CHARS]
        if not text.startswith(self._shown):
            # A quote character disappeared from what we had already shown. Rare,
            # and not worth retracting text over: the finished answer replaces
            # this anyway.
            self._shown = text
            return ""
        fresh = text[len(self._shown) :]
        self._shown = text
        return fresh


async def extract(
    document_text: str,
    *,
    ask: Ask,
    today: date,
    title: str | None = None,
    context: BilagContext | None = None,
) -> Extraction:
    """Ask what the document says, and keep the parts of the answer that hold up.

    ``title`` is the file's own PDF title, shown to the model as a labelled line
    (see :func:`myntos.domain.bilag.classification.excerpt`). It is folded into
    the text the answer is checked against as well, and that is not a loosening
    of the rule: the rule is that the model may only repeat what it was shown,
    and it was shown the title. A receipt whose only mention of the shop is in
    its title would otherwise offer "eBay" as the seller and have it thrown out
    for not being in a body it was never claimed to be in.
    """
    context = context or NO_CONTEXT
    if not document_text.strip() and title is None and context.note is None:
        return Extraction()
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(document_text, title=title, context=context),
        max_tokens=MAX_ANSWER_TOKENS,
    )
    # The document, and only the document. The preamble is emphatically not
    # folded in here the way the title is: the title is a property of the file
    # the user uploaded, while the note is a sentence the user typed, and
    # allowing a quote to be verified against it would let anyone set MOTPART or
    # DATO to anything by writing it in the box. The rule that the model may only
    # repeat what the *bilag* says is the reason its answers are usable at all.
    shown = document_text if title is None else f"{title}\n{document_text}"
    return parse_answer(reply, document_text=shown, today=today)


class AskStream(Protocol):
    """Something that answers one question in pieces, as they are produced."""

    def __call__(self, *, system: str, prompt: str, max_tokens: int) -> AsyncIterator[str]: ...


async def extract_streaming(
    document_text: str,
    *,
    ask: AskStream,
    today: date,
    on_description: Callable[[str], Awaitable[None]],
    title: str | None = None,
    context: BilagContext | None = None,
    on_exchange: Callable[[Exchange], None] | None = None,
) -> Extraction:
    """:func:`extract`, with the description handed over as it is written.

    Identical in what it returns and how it checks: the answer is read by
    :func:`parse_answer` once it is complete, against the same text, under the
    same rules. The only difference is that ``on_description`` is called with
    each new piece of the sentence on the way, so the wait can be spent watching
    it appear.

    **A stream that dies half-way is not an error here.** What arrived is parsed
    as if that were the whole answer, which is exactly what the non-streaming
    path does with a reply the model cut short: the lines that are complete hold
    up, the truncated one fails its own check, and the register keeps its own
    reading for the rest. Raising instead would throw away four good lines
    because a fifth never came.

    ``on_exchange`` is handed the question and the whole answer, for a caller
    that will continue the conversation (:func:`extract_again`), and only when
    the answer arrived whole. A follow-up built on half an answer would show the
    model a reply it never finished as if it had, so a stream that died leaves
    nothing to continue and the follow-up asks afresh.
    """
    context = context or NO_CONTEXT
    if not document_text.strip() and title is None and context.note is None:
        return Extraction()

    pieces = DescriptionStream()
    prompt = build_prompt(document_text, title=title, context=context)
    finished = False
    try:
        async for piece in ask(
            system=_SYSTEM,
            prompt=prompt,
            max_tokens=MAX_ANSWER_TOKENS,
        ):
            fresh = pieces.feed(piece)
            if fresh:
                await on_description(fresh)
        finished = True
    except Exception as error:
        # Named, because this is a blanket catch and a blanket catch that says
        # nothing is a place bugs go to be quiet. The message carries the type
        # and never the prompt.
        logger.info(
            "the model's answer stopped early (%s); reading what arrived", type(error).__name__
        )
    if finished and on_exchange is not None:
        on_exchange(Exchange(system=_SYSTEM, prompt=prompt, reply=pieces.answer))
    shown = document_text if title is None else f"{title}\n{document_text}"
    return parse_answer(pieces.answer, document_text=shown, today=today)


#: The new turn of a follow-up, after what the user said. The five lines are in
#: the first question, which the model sees again word for word, so this only
#: says what is different: the user has spoken, the quotes are still quotes, and
#: an answered question is not asked twice.
_AGAIN = (
    "Svar på nytt med de samme fem linjene, med det brukeren har sagt i tankene. "
    "DATO, VALUTA og MOTPART skal fortsatt skrives av ordrett fra bilaget. "
    "BESKRIVELSE kan ta med det brukeren har fortalt. Ikke still et spørsmål "
    "brukeren allerede har svart på: er det ikke noe annet bare brukeren kan vite, "
    "skriv UKJENT på SPØRSMÅL."
)


async def extract_again(
    earlier: Exchange,
    said: str,
    *,
    ask: AskAfter,
    document_text: str,
    today: date,
    title: str | None = None,
) -> Extraction:
    """Ask the five lines again, after the user answered (:mod:`~myntos.domain.bilag.followup`).

    Read by :func:`parse_answer` against the documents, exactly as the first
    answer was. What the user said is in the prompt and nowhere in the text the
    quotes are checked against, so an answer cannot set the date or the seller
    any more than the note at the top of the first question could.
    """
    prompt = f"{said}\n\n{_AGAIN}" if said else _AGAIN
    reply = await ask(
        system=earlier.system,
        prompt=prompt,
        max_tokens=MAX_ANSWER_TOKENS,
        history=((earlier.prompt, earlier.reply),),
    )
    shown = document_text if title is None else f"{title}\n{document_text}"
    return parse_answer(reply, document_text=shown, today=today)


def merge(reading: InvoiceReading, found: Extraction) -> tuple[InvoiceReading, frozenset[str]]:
    """Fill the gaps in ``reading``, and name the fields that were filled.

    Gaps only. Where the parser read something off the printed page, that stands:
    the model is here for the documents that do not print what we know how to
    look for, not to have a second opinion about the ones that do.

    The date has one exception, and it is the reason this exists at all: when the
    parser took a date merely because it was the earliest one on the page, that
    is a tie-break and not a reading, and a receipt covering a period will lose
    the tie. The model is asked which line is the date and gets to break it.

    The seller has the same kind of exception. A name the parser took from the
    file's title rather than from the page is a gap with something parked in it,
    not a reading: the title is often the filename, and MA-3D's invoice was
    saved as "PI Andreas Lindeman _26-0023", which put the buyer in the motpart
    field ahead of anything the model said. The model, which read the page,
    replaces it.
    """
    filled: dict[str, object] = {}
    if found.occurred_on is not None and (
        reading.invoice_date is None
        or (not reading.date_is_labelled and found.occurred_on != reading.invoice_date)
    ):
        filled["invoice_date"] = found.occurred_on
    # "NOK" means kroner, and kroner is what None means here.
    if (
        reading.currency_code is None
        and found.currency_code is not None
        and found.currency_code != "NOK"
    ):
        filled["currency_code"] = found.currency_code
    if (
        reading.seller_name is None or reading.seller_named_by_title
    ) and found.counterparty_name is not None:
        filled["seller_name"] = found.counterparty_name
    if not filled:
        return reading, frozenset()
    # Not a field the model filled, so not in the set that says which ones it did.
    flags = {"seller_named_by_title": False} if "seller_name" in filled else {}
    return replace(reading, **filled, **flags), frozenset(filled)  # type: ignore[arg-type]


#: Words too short to be worth requiring in a sentence. "1TB" and "M3" are part
#: of a product name; "og" and "à" are joins a fluent sentence rewords away.
_MIN_COVERED_WORD = 3


def _covers(description: str, item_text: str) -> bool:
    """Whether ``description`` says everything the printed item line says.

    Word by word, case folded, and only words of :data:`_MIN_COVERED_WORD`
    characters or more: the question is whether the thing that was bought is
    named in the sentence, not whether the sentence was copied.
    """
    wanted = {word for word in _words(item_text) if len(word) >= _MIN_COVERED_WORD}
    if not wanted:
        return False
    return wanted <= set(_words(description))


def apply_description(
    suggestion: InvoiceSuggestion, found: Extraction, *, item_text: str | None = None
) -> None:
    """Let the model's sentence say what the entry was about, and mark it as its.

    The document's own reference is kept in front of it when there is one, so the
    line in the ledger still points back at the bilag it came from: "Ordre
    10-15068-44333, Apple Mac Studio til kontoret". The reference is what an
    auditor follows; the sentence is what a human reads. The name of the
    counterparty is dropped from the middle, because it is a field of its own on
    the entry and the sentence usually says it anyway.

    ``item_text`` is what the parser read off the item table, and it wins unless
    the model's sentence *contains* it. That is nearly the rule :func:`merge`
    follows for every other field, and the exception is the point. On the eBay
    receipt the parser reads "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB
    SSD 60-Core GPU" and loynd-lite composes "1 item NEW Apple Mac Studio M3
    Ultra … på Economy Int'l Postage GBP 2,137.00", which pads the answer with
    the shipping method and a figure that already has a field — so the printed
    item line is the better source and takes it. But an item line reading
    "Batteri" against a sentence reading "Batteri til verkstedet" is a different
    case: the sentence is not competing with the item line, it is the item line
    plus the context that makes it mean something. Requiring every word of the
    item text to appear in the sentence is what separates the two, and it is a
    check rather than a judgement — a model that dropped the item and wrote
    something else instead loses, as it should.
    """
    if found.description is None:
        return
    if item_text is not None and not _covers(found.description, item_text):
        return
    parts = [part for part in (suggestion.reference, found.description) if part is not None]
    suggestion.description = ", ".join(parts)[:255]
    suggestion.mark("description", FieldSource.MODEL)
