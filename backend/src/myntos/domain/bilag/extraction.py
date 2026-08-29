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
from it.

Everything is optional, exactly as in :mod:`~myntos.domain.bilag.classification`:
an unreachable model, an unparseable answer and an answer contradicted by the
document all end the same way, with the register keeping its own reading.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import date

from myntos.domain.bilag.classification import MAX_DOCUMENT_CHARS, Ask, excerpt
from myntos.domain.bilag.interpretation import (
    FieldSource,
    InvoiceReading,
    InvoiceSuggestion,
    dates_in,
)

logger = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 120
MAX_NAME_CHARS = 120

# Enough for four short lines and not enough for an essay. A model that starts
# explaining itself gets cut off mid-sentence, and a truncated line fails its
# own check rather than arriving as a half-truth.
MAX_ANSWER_TOKENS = 160

_SYSTEM = (
    "Du er en norsk regnskapsassistent. Du får teksten fra et bilag og svarer med "
    "nøyaktig fire linjer på formen NØKKEL: verdi. Du finner ikke på noe: står det "
    "ikke i teksten, svarer du UKJENT. Ingen forklaring og ingen andre linjer."
)

_QUESTION = (
    "Svar med nøyaktig disse fire linjene, i denne rekkefølgen:\n"
    "DATO: datoen kjøpet eller fakturaen er datert, avskrevet nøyaktig slik den står "
    "i teksten over\n"
    "VALUTA: valutakoden beløpene er i, tre store bokstaver, for eksempel NOK, GBP eller EUR\n"
    "MOTPART: navnet på selgeren, avskrevet nøyaktig slik det står i teksten over\n"
    f"BESKRIVELSE: én kort setning på norsk om hva som ble kjøpt, maks "
    f"{MAX_DESCRIPTION_CHARS} tegn\n\n"
    "Skriv UKJENT der teksten ikke sier det. Ikke gjett, og ikke skriv noe annet."
)

# The keys, and only the keys. A line the model invents is not read.
_LINE = re.compile(r"(?im)^[\s*\-]*(DATO|VALUTA|MOTPART|BESKRIVELSE)\s*[:\-]\s*(.+?)\s*$")
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

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.occurred_on, self.currency_code, self.counterparty_name, self.description)
        )


def build_prompt(document_text: str, *, title: str | None = None) -> str:
    """The document, then the four things we want out of it."""
    body = excerpt(document_text, title=title)
    return f"Her er teksten fra et bilag:\n---\n{body}\n---\n{_QUESTION}"


def _tidy(value: str) -> str:
    """One line of an answer, stripped of quoting and of runs of whitespace."""
    return " ".join(value.split()).strip(_QUOTES)


def _said(value: str, document_text: str) -> bool:
    """Whether ``value`` is actually written in the document.

    Compared with whitespace collapsed and case folded, because a PDF text layer
    breaks lines where the layout did and a model quotes what it read rather than
    the bytes it read. Everything else about the comparison is literal: this is
    the check that turns "quote the document" from an instruction into a rule.
    """
    haystack = " ".join(document_text[: MAX_DOCUMENT_CHARS * 2].split()).casefold()
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
    """The currency code, if it is one and if the document says it too."""
    code = value.upper()
    if _CODE.match(code) is None or code not in KNOWN_CURRENCIES:
        return None
    if code == "NOK":
        return "NOK" if _KRONER.search(document_text) is not None else None
    return code if re.search(rf"(?i)\b{code}\b", document_text) is not None else None


def _name(value: str, document_text: str) -> str | None:
    """The seller's name, if the document calls them that."""
    if not 2 <= len(value) <= MAX_NAME_CHARS or len(_LETTER.findall(value)) < 2:
        return None
    return value if _said(value, document_text) else None


def _description(value: str) -> str | None:
    """The sentence, trimmed. The one field with nothing to check it against."""
    if len(value) < 3 or _LETTER.search(value) is None:
        return None
    return value[:MAX_DESCRIPTION_CHARS].strip()


def parse_answer(reply: str, *, document_text: str, today: date) -> Extraction:
    """Read the model's four lines, keeping only what the document backs up."""
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
    return Extraction(
        occurred_on=(
            _date(raw_date, document_text=document_text, today=today)
            if raw_date is not None
            else None
        ),
        currency_code=_currency(raw_currency, document_text) if raw_currency is not None else None,
        counterparty_name=_name(raw_name, document_text) if raw_name is not None else None,
        description=_description(raw_description) if raw_description is not None else None,
    )


async def extract(
    document_text: str, *, ask: Ask, today: date, title: str | None = None
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
    if not document_text.strip() and title is None:
        return Extraction()
    reply = await ask(
        system=_SYSTEM,
        prompt=build_prompt(document_text, title=title),
        max_tokens=MAX_ANSWER_TOKENS,
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
    if reading.seller_name is None and found.counterparty_name is not None:
        filled["seller_name"] = found.counterparty_name
    if not filled:
        return reading, frozenset()
    return replace(reading, **filled), frozenset(filled)  # type: ignore[arg-type]


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

    ``item_text`` is what the parser read off the item table, and when there is
    any, it wins. That is the same rule :func:`merge` follows for every other
    field, and it applies here for the same reason plus a demonstrated one: on
    the eBay receipt the parser reads "NEW Apple Mac Studio M3 Ultra 28-Core 96GB
    RAM 1TB SSD 60-Core GPU" and loynd-lite composes "1 item NEW Apple Mac Studio
    M3 Ultra … på Economy Int'l Postage GBP 2,137.00", which pads the answer with
    the shipping method and a figure that is already its own field. A model is
    here for the documents that print no item table; where one is printed, it is
    the better source.
    """
    if found.description is None or item_text is not None:
        return
    parts = [part for part in (suggestion.reference, found.description) if part is not None]
    suggestion.description = ", ".join(parts)[:255]
    suggestion.mark("description", FieldSource.MODEL)
