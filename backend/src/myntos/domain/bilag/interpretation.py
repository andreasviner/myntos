"""Reading an invoice, so the entry form can fill itself in.

The register asks for four things — direction, type, date, amount — and an
invoice already states three of them in print. This module extracts what the
document says and proposes the corresponding form values; the user confirms them
before anything is posted.

Three rules keep a convenience from turning into a liability:

**Only what is found is proposed.** Every field is optional. When the text does
not say what the payment was for, ``type_code`` comes back ``None`` and the form
keeps whatever the user had chosen — it does not fall back to "annen
driftskostnad" merely to have an answer. The same holds for the VAT code: it is
proposed only when a rate is actually printed, because
:mod:`myntos.domain.ledger.cashflow` already states each type's assumption and a
silent second guess layered on top of that is worse than no guess at all.

**Every proposal is accounted for.** :attr:`InvoiceSuggestion.filled` names the
fields that came from the document, so the UI can show them as read rather than
typed, and :attr:`InvoiceSuggestion.warnings` carries the sentences the user
needs in order to check them — a foreign currency, a date in the future, amounts
that do not add up.

**Nothing here touches the database or the ledger.** The input is a string and a
list of hints; the output is a proposal. Party lookup happens in the API layer
and arrives as :class:`PartyHint`.

The heuristics are built around what Norwegian invoices actually print: ``Å
betale``, ``Fakturadato``, ``Herav mva``, an organisasjonsnummer with a mod-11
check digit, and amounts written ``12 500,00`` or ``12.500,00``.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES, CashFlowDirection
from myntos.domain.ledger.chart import NO_VAT_CODE
from myntos.domain.ledger.errors import InvalidOrgnrError
from myntos.domain.ledger.orgnr import validate_orgnr

# --- Amounts --------------------------------------------------------------------

# The four ways an invoice writes a number, longest form first so the engine does
# not settle for a prefix. The lookbehind and lookahead keep the pattern from
# biting a piece out of something that is not an amount: "14.03.2026" matches no
# branch at all, which is what makes it safe to scan a line without knowing
# whether it holds a date.
_AMOUNT_RE = re.compile(
    r"""
    (?<![\d.,])
    (?:
        \d{1,3}(?:[\ \u00a0\u202f\t]\d{3})+(?:,\d{1,2})?    # 12 500 / 12 500,00
      | \d{1,3}(?:\.\d{3})+(?:,\d{1,2})?                  # 12.500 / 12.500,00
      | \d{1,3}(?:,\d{3})+(?:\.\d{1,2})?                  # 12,500 / 12,500.00
      | \d+(?:[.,]\d{1,2})?                               # 12500 / 12500,00 / 0,25
    )
    (?![.,/-]?\d)
    """,
    re.VERBOSE,
)
_PERCENT_AFTER = re.compile(r"\s*%")
# Ordinary space, no-break space, narrow no-break space, tab: all four turn up as
# thousands separators in PDF text.
_SPACE_CHARACTERS = (" ", "\u00a0", "\u202f", "\t")
_CURRENCY_WORD = re.compile(r"(?i)\b(?:kr|nok)\b\.?")


def parse_amount(raw: str) -> Decimal | None:
    """Read one written number, Norwegian or English convention.

    Which separator is the decimal one is decided by what follows it: three
    digits after the last separator make it a thousands separator, one or two
    make it a decimal comma or point. That reads both ``12.500`` and ``12,500``
    as twelve and a half thousand without having to know the locale of the
    system that produced the invoice.
    """
    text = raw
    for space in _SPACE_CHARACTERS:
        text = text.replace(space, "")
    if not text:
        return None

    separator = max(text.rfind(","), text.rfind("."))
    if separator == -1:
        digits = text
    elif len(text) - separator - 1 == 3:
        digits = text.replace(",", "").replace(".", "")
    else:
        head = text[:separator].replace(",", "").replace(".", "")
        digits = f"{head}.{text[separator + 1 :]}"
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def amounts_in(line: str) -> list[Decimal]:
    """Every amount on one line, in order, skipping VAT rates.

    A figure followed by a percent sign is a rate, not a sum. Dropping those here
    is what lets ``Mva 25 % 312,50`` be read for its 312,50.
    """
    found: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(line):
        if _PERCENT_AFTER.match(line, match.end()) is not None:
            continue
        value = parse_amount(match.group())
        if value is not None:
            found.append(value)
    return found


# --- Dates ----------------------------------------------------------------------

# Three letters is enough to tell Norwegian months apart, and the English ones
# that differ come along for free.
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "oct": 10,
    "nov": 11,
    "des": 12,
    "dec": 12,
}
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DMY_DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})(?!\d)")
_NAMED_DATE = re.compile(r"(?<!\d)(\d{1,2})\.?\s+([a-zæøå]{3})[a-zæøå.]*\s+(\d{4})(?!\d)", re.I)
# "Aug 23, 2026" — the order used by every foreign webshop receipt, and by half
# the ones written in English by Norwegian ones. The day may carry an ordinal
# suffix and the comma may be missing; the year may not.
_MONTH_FIRST_DATE = re.compile(
    r"(?<![a-zæøå0-9])([a-zæøå]{3})[a-zæøå.]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)",
    re.I,
)


def _plausible(year: int, month: int, day: int, *, today: date) -> date | None:
    """A real date, if it could plausibly be an invoice date."""
    if year < 100:
        year += 2000
    # Books do not start before this system's world does, and a document dated
    # years ahead is a misparse rather than an invoice.
    if not 2000 <= year <= today.year + 1:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def dates_in(line: str, *, today: date) -> list[date]:
    """Every plausible date on one line."""
    found: list[date] = []
    for match in _ISO_DATE.finditer(line):
        year, month, day = (int(group) for group in match.groups())
        candidate = _plausible(year, month, day, today=today)
        if candidate is not None:
            found.append(candidate)
    for match in _DMY_DATE.finditer(line):
        day, month, year = (int(group) for group in match.groups())
        candidate = _plausible(year, month, day, today=today)
        if candidate is not None:
            found.append(candidate)
    for match in _NAMED_DATE.finditer(line):
        month = _MONTHS.get(match.group(2).lower(), 0)
        if month == 0:
            continue
        candidate = _plausible(int(match.group(3)), month, int(match.group(1)), today=today)
        if candidate is not None:
            found.append(candidate)
    for match in _MONTH_FIRST_DATE.finditer(line):
        month = _MONTHS.get(match.group(1).lower(), 0)
        if month == 0:
            continue
        candidate = _plausible(int(match.group(3)), month, int(match.group(2)), today=today)
        if candidate is not None and candidate not in found:
            found.append(candidate)
    return found


# --- What the labels look like ---------------------------------------------------

# What the total is called, most specific first. A tier only comes into play when
# no better one matched anywhere in the document, because "Sum" appears on every
# line of an item table while "Å betale" appears once.
_TOTAL_LABELS: tuple[tuple[str, ...], ...] = (
    ("å betale", "a betale", "til betaling", "amount due", "total due", "balance due"),
    (
        "totalt inkl",
        "total inkl",
        "sum inkl",
        "fakturabeløp",
        "totalsum",
        "sum totalt",
        "total amount",
        "grand total",
    ),
    ("totalt", "total", "sum", "beløp"),
)
# Lines stating a figure that is not the gross total, whatever they are called.
_NOT_A_TOTAL = ("eks", "ekskl", "netto", "avrunding", "restbeløp", "purregebyr", "grunnlag")

_NET_LABELS = ("eks mva", "eks. mva", "ekskl mva", "ekskl. mva", "netto", "subtotal", "grunnlag")

_VAT_LABELS = ("herav mva", "sum mva", "mva", "merverdiavgift", "moms", "vat", "avgift")
# "Mva-nr" and "Sum inkl. mva" both contain "mva", and neither states the VAT.
_NOT_VAT = (
    "inkl",
    "mva-nr",
    "mvanr",
    "mva nr",
    "nummer",
    "org",
    "foretaksregister",
    "registrert",
    "å betale",
    "totalt",
    "total",
    "fritatt",
    "uten mva",
    "eks",
    "ekskl",
    "grunnlag",
)

# A receipt has no fakturadato. What it has is the day the money moved, which is
# the day it belongs in the books — so those labels are read as the same thing.
_INVOICE_DATE_LABELS = (
    "fakturadato",
    "faktura dato",
    "bilagsdato",
    "kjøpsdato",
    "kjopsdato",
    "ordredato",
    "betalingsdato",
    "invoice date",
    "utstedt",
    "order date",
    "order placed",
    "placed on",
    "paid on",
    "purchase date",
    "date of purchase",
    "transaction date",
)
_DUE_LABELS = ("forfall", "betalingsfrist", "betales innen", "due date", "payment due")
_GENERIC_DATE_LABELS = ("dato", "date")

# Most specific first, and the bare words last: "Faktura 2026-014" is a number
# and "Fakturadato 14.03.2026" is not, which the trailing lookahead in
# _INVOICE_NUMBER_RE is there to tell apart.
_NUMBER_LABELS = (
    "fakturanummer",
    "fakturanr",
    "faktura nr",
    "invoice number",
    "invoice no",
    "kreditnotanr",
    "fakturaid",
    "ordrenummer",
    "ordrenr",
    "ordre nr",
    "order number",
    "order no",
    "kvitteringsnummer",
    "receipt number",
    "receipt no",
    "faktura",
    "kreditnota",
)
# The labels above that mean the document is an order or a receipt rather than an
# invoice. Only used to name the number in the description: calling an eBay
# receipt "Faktura 10-15068-44333" would be a small lie in the ledger.
_ORDER_LABELS = frozenset(
    {
        "ordrenummer",
        "ordrenr",
        "ordre nr",
        "order number",
        "order no",
        "kvitteringsnummer",
        "receipt number",
        "receipt no",
    }
)
# Who the document is addressed to, for a sales invoice we issued ourselves.
_BUYER_LABELS = ("kunde", "kjøper", "fakturamottaker", "fakturaadresse", "customer", "bill to")
# Who sent it, said outright. A webshop receipt has no organisasjonsnummer to
# anchor the name to, but it does say "Sold by" or "Items bought from".
_SELLER_LABELS = (
    "selger",
    "solgt av",
    "levert av",
    "utstedt av",
    "leverandør",
    "sold by",
    "sold and shipped by",
    "bought from",
    "seller",
)
# The heading of the item table, in the words invoices and receipts actually
# use. Matched as a substring, so "Quantity Item name Shipping service Item
# price" on one line counts: a PDF text layer joins a table header row into one
# string as often as it splits it into several.
_ITEM_HEADINGS = (
    "beskrivelse",
    "varetekst",
    "varelinje",
    "produkt",
    "item name",
    "description",
    "artikkel",
    "tekst",
    "vare",
    "item",
)
# Cell contents that share the table with the item name without being it. Any
# of these appearing as a word in a candidate line disqualifies it.
_ITEM_NOISE = frozenset(
    (
        "sum",
        "total",
        "totalt",
        "subtotal",
        "mva",
        "moms",
        "vat",
        "antall",
        "quantity",
        "qty",
        "pris",
        "price",
        "beløp",
        "amount",
        "enhet",
        "unit",
        "rabatt",
        "discount",
        "frakt",
        "shipping",
        "porto",
        "postage",
        "levering",
        "delivery",
        "stk",
        "kr",
        "nok",
        "sum",
    )
)
_ITEM_MIN_CHARS = 8
_ITEM_MAX_CHARS = 140
# "800545236002", "ART-4471/A": an identifier and nothing else.
_ARTICLE_ONLY = re.compile(r"^[\W\d]*[A-Za-z]{0,4}[-/ ]?\d[\d\-/ ]*[A-Za-z]?[\W\d]*$")
# A currency code and a figure at the end of a row, which is the row's price.
_TRAILING_PRICE = re.compile(
    r"(?i)\s*(?:kr|nok|eur|usd|gbp|sek|dkk|[€$£])?\s*"
    r"\d{1,3}(?:[\s., ]\d{3})*(?:[.,]\d{1,2})?\s*(?:kr|nok|eur|usd|gbp|sek|dkk|[€$£])?\s*$"  # noqa: RUF001
)

_RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%")
_ORGNR_RE = re.compile(r"(?i)(?<!\d)(?:NO\s?)?(\d{3})[ .]?(\d{3})[ .]?(\d{3})(?!\d)")
_INVOICE_NUMBER_RE = re.compile(r"[:\s#]*([A-Za-z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)(?![./-]\d)")
_LETTER = re.compile(r"[A-Za-zÆØÅæøå]")
_NAME_PREFIX = re.compile(r"(?i)^(?:fra|selger|leverandør|utstedt av)\s*[:\-]?\s*")
# A street or postal line, which is what sits between a sender's name and its
# organisasjonsnummer and would otherwise be mistaken for the name.
_ADDRESS_LINE = re.compile(
    r"(?i)\b\d{4}\b|\bpostboks\b|\bp\.?b\.?\s*\d|"
    r"(?:vei|veien|gate|gaten|gata|gt|allé|alle|plass|torg|brygge)\s*\.?\s*\d"
)

# The rates the seeded VAT codes know about, as fractions.
_KNOWN_RATES = (Decimal("0.25"), Decimal("0.15"), Decimal("0.12"))
_TAX_CODE_BY_RATE: dict[CashFlowDirection, dict[Decimal, str]] = {
    CashFlowDirection.UT: {Decimal("0.25"): "1", Decimal("0.15"): "11", Decimal("0.12"): "13"},
    CashFlowDirection.INN: {Decimal("0.25"): "3", Decimal("0.15"): "31", Decimal("0.12"): "32"},
}
_SALES_TYPE_BY_RATE = {
    Decimal("0.25"): "salg_hoy_sats",
    Decimal("0.15"): "salg_middels_sats",
    Decimal("0.12"): "salg_lav_sats",
}

# What a purchase was for, read off the document. Most specific first: a power
# bill says "abonnement" too, and the first entry that matches wins.
_PURCHASE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("strom", ("nettleie", "strømforbruk", "kwh", "fjernvarme", "elektrisk kraft", "strøm")),
    ("husleie", ("husleie", "leie av lokale", "leie av lokaler", "felleskostnader", "leietaker")),
    ("forsikring", ("forsikringspremie", "forsikringsavtale", "forsikring", "polise")),
    ("telefon_internett", ("mobilabonnement", "telefoni", "bredbånd", "internett", "mobilnummer")),
    (
        "datakostnad",
        (
            "programvare",
            "software",
            "lisens",
            "license",
            "subscription",
            "hosting",
            "domene",
            "skytjeneste",
            "saas",
        ),
    ),
    (
        "fremmed_tjeneste",
        ("regnskapsfører", "regnskapsføring", "revisjon", "revisor", "advokat", "juridisk bistand"),
    ),
    (
        "markedsforing",
        ("markedsføring", "annonsering", "annonse", "reklame", "google ads", "profilering"),
    ),
    (
        "bilkostnad",
        ("drivstoff", "bensin", "diesel", "bompenger", "bomring", "dekkskift", "verksted"),
    ),
    ("reise", ("flybillett", "overnatting", "hotellopphold", "hotell", "togbillett", "leiebil")),
    ("kontorrekvisita", ("kontorrekvisita", "rekvisita", "toner", "blekkpatron", "kopipapir")),
    ("bankgebyr", ("kortgebyr", "bankgebyr", "transaksjonsgebyr")),
    ("inventar", ("verktøy", "inventar", "møbler", "kontorstol")),
    ("varekjop", ("varekjøp", "varelinjer", "engros", "videresalg")),
)

_TITLE_WORDS = ("faktura", "kreditnota", "invoice", "kvittering", "purring", "betalingsvarsel")
# Section headings. They sit exactly where a sender's name sits — at the top of
# the page, alone on their line, in letters — and a receipt from a webshop is
# made of them. "Order information" is not a company.
_HEADINGS = (
    "order information",
    "order details",
    "order total",
    "order summary",
    "shipping address",
    "shipping service",
    "billing address",
    "delivery address",
    "payment method",
    "payment details",
    "item name",
    "item price",
    "items bought",
    "quantity",
    "subtotal",
    "ordreinformasjon",
    "ordredetaljer",
    "leveringsadresse",
    "fakturaadresse",
    "betalingsmåte",
    "betalingsinformasjon",
    "beskrivelse",
    "antall",
    "sum",
    "totalt",
    "beløp",
)
_LEGAL_FORMS = (
    "as",
    "asa",
    "ans",
    "da",
    "nuf",
    "sa",
    "ba",
    "enk",
    "ab",
    "oy",
    "gmbh",
    "ltd",
    "inc",
)

# A foreign currency is only reported when nothing says kroner: an invoice that
# writes "NOK" is in kroner even if the word "euro" turns up in a footer.
_CURRENCIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EUR", re.compile(r"(?i)\beur\b|\beuro\b|€")),
    ("USD", re.compile(r"(?i)\busd\b|us\$|\$\s?\d")),
    ("GBP", re.compile(r"(?i)\bgbp\b|£")),
    ("SEK", re.compile(r"(?i)\bsek\b")),
    ("DKK", re.compile(r"(?i)\bdkk\b")),
)
_NOK_MARK = re.compile(r"(?i)\bnok\b|\bkr\b|kroner")


# --- What the document says ------------------------------------------------------


@dataclass(frozen=True)
class InvoiceReading:
    """What the text of a document appears to state. No decisions yet."""

    invoice_date: date | None = None
    # False when the date is the earliest one on the page rather than one sitting
    # behind a label. Both are proposals; only one of them is a reading.
    date_is_labelled: bool = False
    due_date: date | None = None
    gross_amount: Decimal | None = None
    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    # A fraction, snapped to a rate the seeded VAT codes know: 0.25, 0.15, 0.12.
    vat_rate: Decimal | None = None
    invoice_number: str | None = None
    # What the number is called on the document: ``None`` for a faktura, "Ordre"
    # for an order or a receipt. Only ever used to word the description.
    number_kind: str | None = None
    # Every organisasjonsnummer with a valid check digit, in the order printed.
    orgnrs: tuple[str, ...] = ()
    seller_name: str | None = None
    # Only looked for behind a "Kunde"-style label, and only useful when the
    # seller turns out to be us.
    buyer_name: str | None = None
    currency_code: str | None = None
    is_credit_note: bool = False
    # The first line under the item table's heading: what was actually bought.
    # The one thing on a bilag that answers "hva gjaldt det" without a model
    # having to compose a sentence, and the reason a description can say
    # "Apple Mac Studio M3 Ultra" rather than repeating the order number.
    item_text: str | None = None
    # What the file says about itself, passed in from the PDF's metadata rather
    # than read out of the body. Never used for an amount or a date, only for
    # the two questions it can answer: who sold this, and what the model should
    # be told the document is.
    document_title: str | None = None


def _is_bare_amount(line: str) -> bool:
    """True for a line holding nothing but a figure — ``1 562,50``, ``kr 1 562,50``."""
    if not amounts_in(line):
        return False
    remainder = _CURRENCY_WORD.sub("", _AMOUNT_RE.sub("", line))
    return _LETTER.search(remainder) is None


def _labelled_amount(
    lines: list[str],
    labels: tuple[str, ...],
    *,
    exclude: tuple[str, ...] = (),
    allow_zero: bool = False,
) -> Decimal | None:
    """The amount belonging to a label, taking the largest of the candidates.

    The **last** figure on the line is the one taken: ``25 % mva av 12 500,00
    312,50`` states the basis before the amount, and a layout that prints more
    than one figure prints the one it is talking about last. When the label sits
    alone on its line — which happens when a PDF's text comes out column by
    column — the following line is used if it holds nothing but a figure.

    ``allow_zero`` is for the VAT line only: a total of nothing is a misreading,
    while VAT of nothing is a fact worth knowing — it is how an invoice from
    abroad, or from a business below the registration threshold, looks.
    """
    best: Decimal | None = None
    for index, line in enumerate(lines):
        low = line.lower()
        if not any(label in low for label in labels):
            continue
        if any(word in low for word in exclude):
            continue
        found = amounts_in(line)
        value = found[-1] if found else None
        if value is None:
            for following in lines[index + 1 : index + 3]:
                if _is_bare_amount(following):
                    value = amounts_in(following)[-1]
                    break
        if value is None or value < 0 or (value == 0 and not allow_zero):
            continue
        best = value if best is None else max(best, value)
    return best


def _total(lines: list[str]) -> Decimal | None:
    for labels in _TOTAL_LABELS:
        found = _labelled_amount(lines, labels, exclude=_NOT_A_TOTAL)
        if found is not None:
            return found
    return None


def _printed_rate(lines: list[str]) -> Decimal | None:
    """A percentage printed on a VAT line, as a fraction."""
    for line in lines:
        low = line.lower()
        if not any(label in low for label in _VAT_LABELS):
            continue
        match = _RATE_RE.search(line)
        if match is None:
            continue
        value = parse_amount(match.group(1))
        if value is not None and 0 < value <= 100:
            return value / 100
    return None


def _snap_rate(rate: Decimal | None) -> Decimal | None:
    """A rate rounded to one we have a VAT code for, or nothing."""
    if rate is None:
        return None
    for known in _KNOWN_RATES:
        if abs(rate - known) <= Decimal("0.005"):
            return known
    return None


def _dates(lines: list[str], *, today: date) -> tuple[date | None, date | None, bool]:
    """The invoice date, the due date, and whether the first one was labelled.

    The due date is the one date on an invoice that must never become the
    posting date, so lines naming it are excluded from the search for the other.
    Failing an explicit ``Fakturadato``, a line labelled merely ``Dato`` is used,
    and failing that the earliest date left in the document — an invoice is dated
    before it falls due and before the period it covers ends.

    That last one is a tie-break rather than a reading, and it says so: the
    third value is ``False`` when the date came from nothing but being the
    earliest. :mod:`myntos.domain.bilag.extraction` is allowed to overrule a
    tie-break, and is not allowed to overrule a label.
    """
    due: date | None = None
    labelled: date | None = None
    generic: date | None = None
    loose: list[date] = []

    for line in lines:
        low = line.lower()
        found = dates_in(line, today=today)
        if not found:
            continue
        if any(label in low for label in _DUE_LABELS):
            due = due if due is not None else found[0]
        elif any(label in low for label in _INVOICE_DATE_LABELS):
            labelled = labelled if labelled is not None else found[0]
        elif any(label in low for label in _GENERIC_DATE_LABELS):
            generic = generic if generic is not None else found[0]
        else:
            loose.extend(found)

    if labelled is not None:
        return labelled, due, True
    if generic is not None:
        return generic, due, True
    return (min(loose) if loose else None), due, False


def _invoice_number(lines: list[str]) -> tuple[str | None, str | None]:
    """The document's own number, and the word for what kind of number it is.

    The word is ``None`` for an invoice, which is what the description already
    assumes, and ``"Ordre"`` when the label said so — so that a webshop receipt
    is described as the order it is.
    """
    for line in lines:
        low = line.lower()
        for label in _NUMBER_LABELS:
            position = low.find(label)
            if position == -1:
                continue
            match = _INVOICE_NUMBER_RE.match(line, position + len(label))
            if match is not None and len(match.group(1)) <= 32:
                return match.group(1), ("Ordre" if label in _ORDER_LABELS else None)
    return None, None


def _orgnrs(text: str) -> tuple[str, ...]:
    """Every organisasjonsnummer in the text, deduplicated, order preserved.

    The mod-11 check digit is what makes this usable at all: nine digits also
    turn up in reference and phone numbers, and only about one in eleven of those
    passes.
    """
    found: list[str] = []
    for match in _ORGNR_RE.finditer(text):
        digits = "".join(match.groups())
        try:
            valid = validate_orgnr(digits)
        except InvalidOrgnrError:
            continue
        if valid not in found:
            found.append(valid)
    return tuple(found)


def _clean_name(line: str) -> str | None:
    """A line reduced to a company name, or ``None`` if it is not one."""
    cleaned = _NAME_PREFIX.sub("", line.strip()).strip(" .:,-|")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 120:
        return None
    if any(word == low or low.startswith(f"{word} ") for word in _TITLE_WORDS + _HEADINGS):
        return None
    if _ADDRESS_LINE.search(cleaned) is not None:
        return None
    # Mostly letters, or it is an address line or a total rather than a name.
    letters = len(_LETTER.findall(cleaned))
    return cleaned if letters >= 2 and letters >= len(cleaned) / 2 else None


def _seller_name(lines: list[str], orgnr: str | None) -> str | None:
    """Who issued the document.

    The name sits next to the organisasjonsnummer far more reliably than it sits
    in any fixed position on the page, so that is where it is looked for first:
    on the same line as the number, then in the few lines above it. Those are
    read **top down**, because a sender block is written name first and address
    afterwards — the line closest to the organisasjonsnummer is usually the
    postal code, not the company.

    A receipt from a foreign webshop has no organisasjonsnummer at all, and that
    is what the second attempt is for: "Sold by", "Items bought from", "Selger".
    The top of the page is the last resort, because on a receipt the top of the
    page is a heading.
    """
    if orgnr is not None:
        printed = re.compile(
            r"(?i)(?:org\.?\s*nr\.?|organisasjonsnummer|foretaksregisteret|NO)?[:\s]*"
            + r"[ .]?".join(orgnr)
            + r"\s*(?:MVA)?",
        )
        for index, line in enumerate(lines):
            if printed.search(line) is None:
                continue
            remainder = _clean_name(printed.sub("", line))
            if remainder is not None:
                return remainder
            for above in lines[max(0, index - 4) : index]:
                candidate = _clean_name(above)
                if candidate is not None:
                    return candidate
            break

    labelled = _labelled_name(lines, _SELLER_LABELS)
    if labelled is not None:
        return labelled

    for line in lines[:8]:
        low = line.lower()
        # A line naming the recipient is the one name on the page that is
        # certainly not the sender's.
        if any(label in low for label in _BUYER_LABELS):
            continue
        candidate = _clean_name(line)
        if candidate is not None:
            return candidate
    return None


def _labelled_name(lines: list[str], labels: tuple[str, ...]) -> str | None:
    """The name written after one of ``labels``, or on the line below it.

    The line below is worth trying because a label alone on its line is what a
    two-column layout looks like once the text layer has been flattened.
    """
    for index, line in enumerate(lines):
        low = line.lower()
        for label in labels:
            position = low.find(label)
            if position == -1:
                continue
            candidate = _clean_name(line[position + len(label) :])
            if candidate is not None:
                return candidate
            following = lines[index + 1] if index + 1 < len(lines) else ""
            candidate = _clean_name(following)
            if candidate is not None:
                return candidate
    return None


def _buyer_name(lines: list[str]) -> str | None:
    """Who the document is addressed to.

    Only useful for an invoice we issued ourselves, where the counterparty is the
    customer rather than the sender — but that is exactly the case where the
    sender's name is our own and therefore no help.
    """
    return _labelled_name(lines, _BUYER_LABELS)


def _item_text(lines: list[str]) -> str | None:
    """What was bought, taken from the first line under the item table.

    The heading of the item table is the one piece of structure nearly every
    invoice and receipt shares, in both languages: "Beskrivelse", "Varetekst",
    "Item name", "Description". Whatever it is called, the line under it is the
    thing the money was for, and the thing that belongs in the description of
    the ledger entry.

    **Anchored on the heading, and nowhere else.** The alternative is to hunt
    the page for the longest human-looking line, which finds the seller's
    address on one invoice and a payment instruction on the next. A heading is
    evidence; a line's length is not. So a document without one of these
    headings yields nothing here, and the description falls back to naming the
    counterparty exactly as it did before.

    Quantity columns and article numbers are skipped on the way down, because a
    PDF text layer breaks a table row into one line per cell and the item name
    is rarely the first cell out.
    """
    for index, line in enumerate(lines):
        low = line.lower()
        if not any(heading in low for heading in _ITEM_HEADINGS):
            continue
        # A heading names columns; it does not hold figures. Requiring that is
        # what tells the column header "Quantity Item name Shipping service Item
        # price" apart from the summary line "1 item GBP 2,137.00" a few lines
        # above it, which contains the word "item" and is not a heading at all.
        if amounts_in(line):
            continue
        for candidate in lines[index + 1 : index + 8]:
            item = _clean_item(candidate)
            if item is not None:
                return item
        return None
    return None


def _clean_item(line: str) -> str | None:
    """One line of an item table, if it reads as the name of a thing."""
    # A price at the end of the row is the row's price, not part of its name.
    # Trimmed before anything else so that what follows judges the words.
    cleaned = _TRAILING_PRICE.sub("", " ".join(line.split())).strip(" .:,-|*")
    if not _ITEM_MIN_CHARS <= len(cleaned) <= _ITEM_MAX_CHARS:
        return None
    words = {word for word in re.split(r"[^0-9a-zæøå]+", cleaned.lower()) if word}
    # Anywhere in the line, not merely at the start: "Order total GBP" is a
    # totals row wherever the word "total" happens to sit in it, and a totals
    # row directly under a heading is exactly what a receipt without a proper
    # item table looks like.
    if words & _ITEM_NOISE:
        return None
    if _is_bare_amount(cleaned) or _ARTICLE_ONLY.match(cleaned) is not None:
        return None
    letters = len(_LETTER.findall(cleaned))
    # A third letters keeps "Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD
    # 60-Core GPU" and drops "1 2 137,00 25 % 534,25".
    if letters < 6 or letters < len(cleaned) / 3:
        return None
    return cleaned


def _currency(text: str) -> str | None:
    if _NOK_MARK.search(text) is not None:
        return None
    for code, pattern in _CURRENCIES:
        if pattern.search(text) is not None:
            return code
    return None


def read(text: str, *, today: date, title: str | None = None) -> InvoiceReading:
    """Extract what ``text`` states, without deciding anything about it.

    ``title`` is what the file says about itself, from the PDF's own metadata.
    It is kept out of ``text`` on purpose rather than pasted on top of it: every
    amount, date and organisasjonsnummer in here is found by looking at lines,
    and a line that did not come off the page has no business being one of them.
    It is used for exactly two things, both of which are named where they
    happen: the seller's name when the body does not give one, and the sentence
    the model is shown alongside the excerpt.
    """
    document_title = _clean_title(title)
    if not text.strip():
        return InvoiceReading(document_title=document_title, seller_name=document_title)

    lines = [line.strip() for line in text.splitlines()]
    gross = _total(lines)
    net = _labelled_amount(lines, _NET_LABELS)
    vat = _labelled_amount(lines, _VAT_LABELS, exclude=_NOT_VAT, allow_zero=True)

    # Any two of the three give the third, and invoices state at least two.
    if gross is None and net is not None and vat is not None:
        gross = net + vat
    elif net is None and gross is not None and vat is not None:
        net = gross - vat

    rate = _printed_rate(lines)
    if rate is None and vat is not None and net is not None and net > 0:
        rate = vat / net

    invoice_date, due_date, date_is_labelled = _dates(lines, today=today)
    number, number_kind = _invoice_number(lines)
    orgnrs = _orgnrs(text)
    low = text.lower()

    return InvoiceReading(
        invoice_date=invoice_date,
        date_is_labelled=date_is_labelled,
        due_date=due_date,
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        vat_rate=Decimal(0) if vat == 0 else _snap_rate(rate),
        invoice_number=number,
        number_kind=number_kind,
        orgnrs=orgnrs,
        seller_name=_seller_name(lines, orgnrs[0] if orgnrs else None) or document_title,
        buyer_name=_buyer_name(lines),
        currency_code=_currency(text),
        is_credit_note="kreditnota" in low or "credit note" in low,
        item_text=_item_text(lines),
        document_title=document_title,
    )


# A PDF title is set by whatever produced the file, so it arrives as a heading, a
# browser tab or a filename: "Order details | eBay", "Order details eBay",
# "faktura_104588.pdf". What is wanted out of all three is the part that names
# somebody, so the furniture around it is stripped: the extension, the separator
# a browser puts between the page and the site, and the word for what kind of
# document this is. When nothing survives, nothing is returned — a title that
# only says "kvittering" tells us what we already knew.
_TITLE_TAIL = re.compile(r"\s*[|\-–—·]\s*")  # noqa: RUF001
_TITLE_EXTENSION = re.compile(r"(?i)\.(pdf|docx?|html?|txt)$")
_TITLE_KIND = re.compile(
    r"(?i)^(?:order\s+details|order\s+confirmation|order|receipt|invoice|"
    r"ordrebekreftelse|kjøpsbekreftelse|kvittering|faktura|bilag|dokument|document|"
    # Not \b: an underscore is a word character, so \b would refuse to end the
    # word in "faktura_104588" and leave an invoice number standing in for a
    # company name.
    r"scan|scanned|download)(?![a-zæøå0-9])[\s:_.-]*"
)


def _clean_title(title: str | None) -> str | None:
    """The part of a document title worth treating as a name."""
    if title is None:
        return None
    collapsed = _TITLE_EXTENSION.sub("", " ".join(title.split()))
    parts = [part.strip() for part in _TITLE_TAIL.split(collapsed) if part.strip()]
    # Last part first: a title reads "what this is | who it is from", and it is
    # the second half that names anybody.
    for part in reversed(parts):
        candidate = _TITLE_KIND.sub("", part).strip(" .:,-|_")
        if 2 <= len(candidate) <= 80 and len(_LETTER.findall(candidate)) >= 2:
            return candidate
    return None


# --- From what it says to what the form should hold -------------------------------


@dataclass(frozen=True)
class PartyHint:
    """One registered customer or supplier, as far as matching needs to know."""

    id: int
    name: str
    orgnr: str | None
    is_customer: bool


class FieldSource(enum.StrEnum):
    """Where one proposed field came from.

    Shown to the user rather than kept for statistics: "this is the line the
    document prints" and "a model read this out of it" are different degrees of
    confidence, and the person about to post the entry is entitled to know which
    one they are looking at, field by field.
    """

    DOCUMENT = "dokument"
    MODEL = "ki"
    # Neither read nor guessed: worked out from something read, by arithmetic
    # the user could repeat. A total in GBP multiplied by a published Norges
    # Bank rate is the only thing that lands here, and it is kept distinct from
    # DOCUMENT because the document does not say it and from MODEL because no
    # model was involved. The user is owed the difference: a figure they can
    # check against a rate table is not the same kind of proposal as a sentence
    # a model wrote.
    COMPUTED = "beregnet"


@dataclass
class InvoiceSuggestion:
    """Proposed form values. Every field is optional, and the user confirms."""

    direction: CashFlowDirection = CashFlowDirection.UT
    type_code: str | None = None
    tax_code: str | None = None
    occurred_on: date | None = None
    gross_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    description: str | None = None
    party_id: int | None = None
    counterparty_name: str | None = None
    counterparty_orgnr: str | None = None
    invoice_number: str | None = None
    # How the document refers to itself, as a phrase: "Faktura 104588", "Ordre
    # 10-15068-44333". It is the front half of the description, and it is kept
    # separately so that a description written by a model can be put behind the
    # same reference instead of replacing it.
    reference: str | None = None
    currency_code: str | None = None
    # The total as the document prints it, when that is not in kroner. Kept out
    # of `gross_amount` on purpose: the ledger is in kroner, so 2 137,00 GBP is
    # not a number this form may be filled in with — but it is the number the
    # user needs in front of them while they look up what the account was
    # actually charged.
    foreign_amount: Decimal | None = None
    # The Norges Bank rate the kroner amount was worked out with, and the day it
    # was quoted for. Both are shown next to the amount rather than kept for
    # ourselves: a converted figure the user cannot check is a figure they have
    # to redo by hand, which is the situation the conversion was meant to end.
    exchange_rate: Decimal | None = None
    rate_quoted_on: date | None = None
    # Which fields were proposed rather than typed, and where each one came
    # from. The UI marks them accordingly instead of presenting a guess as if
    # the user had made it.
    sources: dict[str, FieldSource] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # The subset of `warnings` that belongs to the current category. Kept apart
    # so that replacing the category (apply_type) takes the previous one's advice
    # with it instead of leaving contradictory sentences behind.
    type_warnings: list[str] = field(default_factory=list)
    # The same arrangement for the sentence about foreign currency, which is
    # rewritten rather than added to once a rate has been found: "beløpsfeltet
    # står tomt med vilje" must not survive next to a filled-in amount.
    currency_warnings: list[str] = field(default_factory=list)
    # And for the driftsmiddel threshold, which depends on both the amount and
    # the category and so has to be redone whenever either of them moves.
    capitalisation_warnings: list[str] = field(default_factory=list)

    @property
    def filled(self) -> list[str]:
        """The fields that were proposed, in the order they were proposed."""
        return list(self.sources)

    @property
    def type_source(self) -> FieldSource | None:
        """Where the category came from, which is the field it matters most for."""
        return self.sources.get("type_code")

    def mark(self, name: str, source: FieldSource = FieldSource.DOCUMENT) -> None:
        """Record that ``name`` was proposed, and by whom."""
        self.sources[name] = source


def _written(value: Decimal) -> str:
    """A number the Norwegian way, for a sentence: ``2 137,00``."""
    # Thousands separated by a non-breaking space and decimals by a comma:
    # nb-NO, which is how the rest of the interface writes a sum.
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


def _written_rate(value: Decimal) -> str:
    """A rate the Norwegian way, at the four decimals Norges Bank quotes.

    Not :func:`_written`, and the difference matters: rounding 12,6853 to 12,69
    in the sentence would make the multiplication the user is invited to check
    come out to a different number than the one in the field.
    """
    return f"{value:,.4f}".replace(",", "\u00a0").replace(".", ",")


def _normalise_name(name: str) -> str:
    """A company name reduced to what is worth comparing."""
    words = [word for word in re.split(r"[^0-9a-zæøå]+", name.lower()) if word]
    while words and words[-1] in _LEGAL_FORMS:
        words.pop()
    return "".join(words)


def _match_party(
    hints: list[PartyHint], *, orgnr: str | None, name: str | None
) -> PartyHint | None:
    """The registered party this document is with, if we already know them."""
    if orgnr is not None:
        for hint in hints:
            if hint.orgnr == orgnr:
                return hint
    if name is not None:
        target = _normalise_name(name)
        # Short names match too much: "Ås" would match half a register.
        if len(target) >= 4:
            for hint in hints:
                own = _normalise_name(hint.name)
                if own and (own == target or own in target or target in own):
                    return hint
    return None


def _purchase_type(text: str) -> str | None:
    low = text.lower()
    for code, keywords in _PURCHASE_KEYWORDS:
        if any(keyword in low for keyword in keywords):
            return code
    return None


def apply_type(
    suggestion: InvoiceSuggestion,
    *,
    reading: InvoiceReading,
    type_code: str | None,
    source: FieldSource | None,
) -> None:
    """Set the category, and everything that follows from it.

    Separate from :func:`suggest` and re-runnable, because the category can come
    from two places: the words in the document, or a model asked to choose from
    the catalogue (:mod:`myntos.domain.bilag.classification`). Whichever answers,
    the consequences are the same and are worked out here — the direction, the
    VAT code, and the sentences the user needs to see next to them.

    Calling it a second time replaces the first answer completely, advice
    included. A stale warning about the previous category would be worse than no
    warning at all.
    """
    for stale in suggestion.type_warnings:
        if stale in suggestion.warnings:
            suggestion.warnings.remove(stale)
    suggestion.type_warnings = []
    for name in ("type_code", "tax_code"):
        suggestion.sources.pop(name, None)
    suggestion.type_code = type_code
    suggestion.tax_code = None

    def warn(sentence: str) -> None:
        suggestion.type_warnings.append(sentence)
        suggestion.warnings.append(sentence)

    entry = CASH_FLOW_TYPES[type_code] if type_code is not None else None
    if entry is not None:
        # The direction follows the category rather than the other way round: a
        # category belongs to exactly one direction, so letting them disagree
        # would put the form in a state no posting could come out of.
        suggestion.direction = entry.direction
        suggestion.mark("type_code", source or FieldSource.DOCUMENT)
    else:
        warn(
            "Vi fant ikke ut hva dette gjelder. Velg type selv. Den bestemmer hvilken konto "
            "beløpet havner på, og dermed hva Skatteetaten får se."
        )

    if reading.currency_code is not None:
        # Whatever tax the seller charged in GBP, it is not norsk inngående mva
        # and there is no code here that would make it deductible. The purchase
        # may owe Norwegian VAT the other way round, which is the user's to
        # settle and not something a form may assume on their behalf.
        warn(
            f"Bilaget er i {reading.currency_code}, så avgiften på det er ikke norsk inngående "
            "mva. Velg mva-kode selv. Kjøp fra utlandet kan utløse innførselsmva eller omvendt "
            "avgiftsplikt."
        )
        return

    rate = reading.vat_rate
    default = entry.default_tax_code if entry is not None else None
    if rate is not None and rate > 0:
        code = _TAX_CODE_BY_RATE[suggestion.direction].get(rate)
        if code is None:
            return
        if default == NO_VAT_CODE:
            # The category assumes no VAT treatment on purpose — a passenger
            # car, a trip abroad, an insurance premium. That the seller charged
            # VAT does not make it deductible, so the assumption stands and the
            # user is told what the document said instead.
            label = entry.label if entry is not None else ""
            warn(
                f"Fakturaen viser {int(rate * 100)} % mva, men «{label}» er satt opp uten "
                "fradrag fordi fradragsretten avhenger av bruken. Velg inngående mva selv "
                "hvis dette kjøpet gir fradrag."
            )
            return
        suggestion.tax_code = code
        suggestion.mark("tax_code")
        if default is not None and default != code:
            warn(
                f"Fakturaen viser {int(rate * 100)} % mva, så mva-koden er satt etter "
                "fakturaen og ikke etter typen."
            )
    elif reading.vat_amount == 0:
        warn(
            "Fakturaen ser ut til å være uten mva. Kontroller mva-koden. Kjøp fra utlandet "
            "kan utløse omvendt avgiftsplikt."
        )


def suggest(
    text: str,
    *,
    today: date,
    own_orgnr: str,
    own_name: str,
    parties: list[PartyHint] | None = None,
    reading: InvoiceReading | None = None,
    model_fields: frozenset[str] = frozenset(),
) -> InvoiceSuggestion:
    """Propose form values for the invoice in ``text``.

    ``own_orgnr`` and ``own_name`` identify the company being booked for, and are
    what makes the direction guessable at all: a document naming us before it
    names anyone else is one we issued, and therefore money in. Everything else
    is treated as a purchase, which is what the large majority of uploaded
    invoices are.

    ``model_fields`` names the attributes of ``reading`` that a model supplied
    rather than the parser (see :mod:`myntos.domain.bilag.extraction`), so that
    the proposals built on them are marked as the model's rather than the
    document's.
    """
    hints = parties or []

    def source_of(*names: str) -> FieldSource:
        """Whether the reading a field was built from came from the model."""
        if any(name in model_fields for name in names):
            return FieldSource.MODEL
        return FieldSource.DOCUMENT

    found = reading if reading is not None else read(text, today=today)
    suggestion = InvoiceSuggestion(
        invoice_number=found.invoice_number, currency_code=found.currency_code
    )

    if not text.strip():
        suggestion.warnings.append(
            "Vi fant ingen tekst i PDF-en. Er den skannet eller fotografert, må du fylle "
            "ut feltene selv. Dokumentet blir lagret som bilag uansett."
        )
        return suggestion

    # --- Who it is with, which is also the best evidence of direction ---
    counterparty_orgnr = next((orgnr for orgnr in found.orgnrs if orgnr != own_orgnr), None)
    named_first = bool(found.orgnrs) and found.orgnrs[0] == own_orgnr
    seller_is_us = found.seller_name is not None and _normalise_name(
        found.seller_name
    ) == _normalise_name(own_name)
    we_issued_it = named_first or seller_is_us
    # On an invoice we sent, the counterparty is the customer it is addressed to;
    # on one we received, it is whoever sent it.
    other_name = found.buyer_name if we_issued_it else found.seller_name

    party = _match_party(hints, orgnr=counterparty_orgnr, name=other_name)
    if party is not None:
        suggestion.direction = CashFlowDirection.INN if party.is_customer else CashFlowDirection.UT
        suggestion.party_id = party.id
        suggestion.counterparty_name = party.name
        # Matched on the organisasjonsnummer, and the number is printed: the
        # party is then ours to be sure about however the name was arrived at.
        by_orgnr = counterparty_orgnr is not None and party.orgnr == counterparty_orgnr
        source = FieldSource.DOCUMENT if by_orgnr else source_of("seller_name")
        suggestion.mark("direction", source)
        suggestion.mark("party_id", source)
    elif we_issued_it:
        suggestion.direction = CashFlowDirection.INN
        suggestion.mark("direction")

    if suggestion.counterparty_name is None:
        suggestion.counterparty_name = other_name
    suggestion.counterparty_orgnr = counterparty_orgnr

    # --- Date ---
    if found.invoice_date is not None:
        suggestion.occurred_on = found.invoice_date
        suggestion.mark("occurred_on", source_of("invoice_date"))
        if found.invoice_date > today:
            suggestion.warnings.append(
                "Datoen vi fant ligger fram i tid. Bokfør på datoen betalingen faktisk skjedde."
            )
    else:
        suggestion.warnings.append("Vi fant ingen fakturadato. Velg dato selv.")

    # --- Amount ---
    # A total in a foreign currency is read and shown, but never proposed: the
    # ledger is in kroner, and 2 137 written into a kroner field is off by
    # whatever the exchange rate was. What the account was charged is on the
    # bank statement, not on the document, so that is the one field a foreign
    # bilag leaves for the user.
    if found.gross_amount is not None and found.currency_code is not None:
        suggestion.foreign_amount = found.gross_amount
    elif found.gross_amount is not None:
        suggestion.gross_amount = found.gross_amount
        suggestion.vat_amount = found.vat_amount
        suggestion.mark("gross_amount")
        if (
            found.net_amount is not None
            and found.vat_amount is not None
            and abs(found.gross_amount - (found.net_amount + found.vat_amount)) > 1
        ):
            suggestion.warnings.append(
                "Beløpene på fakturaen summerer seg ikke helt. Kontroller totalen."
            )
    elif found.currency_code is None:
        suggestion.warnings.append("Vi fant ikke totalbeløpet. Skriv det inn selv.")

    # --- What it was for, and the VAT code that follows from it ---
    if suggestion.direction is CashFlowDirection.INN:
        # A sale is categorised by its rate: that is the only thing on an
        # outgoing invoice that decides which salgsinntekt account it belongs to.
        rate = found.vat_rate
        heuristic = _SALES_TYPE_BY_RATE.get(rate) if rate is not None and rate > 0 else None
    else:
        heuristic = _purchase_type(text)
    apply_type(
        suggestion,
        reading=found,
        type_code=heuristic,
        source=FieldSource.DOCUMENT if heuristic is not None else None,
    )

    # --- Description ---
    if found.invoice_number is not None:
        kind = "Kreditnota" if found.is_credit_note else found.number_kind or "Faktura"
        suggestion.reference = f"{kind} {found.invoice_number}"
    # What was bought beats who sold it. The counterparty has a field of its own
    # a few centimetres away, so spending the description on it says the same
    # thing twice and leaves "hva gjaldt det" unanswered — which is the
    # complaint "Ordre 10-15068-44333, daisyaus-0" earns and deserves.
    tail = found.item_text or suggestion.counterparty_name
    parts = [part for part in (suggestion.reference, tail) if part is not None]
    if parts:
        suggestion.description = ", ".join(parts)[:255]
        suggestion.mark(
            "description",
            FieldSource.DOCUMENT if found.item_text is not None else source_of("seller_name"),
        )

    # --- Things worth saying out loud ---
    if found.currency_code is not None:
        printed = (
            f", og totalen er {_written(found.gross_amount)} {found.currency_code}"
            if found.gross_amount is not None
            else ""
        )
        _warn_currency(
            suggestion,
            f"Bilaget er i {found.currency_code}, ikke kroner{printed}. Hovedboken føres i "
            "kroner, så beløpsfeltet står tomt med vilje: skriv inn beløpet som faktisk ble "
            "belastet kontoen, kurs og gebyrer inkludert.",
        )
    if found.is_credit_note:
        suggestion.warnings.append(
            "Dette ser ut som en kreditnota. Den føres i motsatt retning av fakturaen den gjelder."
        )
    note_capitalisation(suggestion)
    return suggestion


def _warn_currency(suggestion: InvoiceSuggestion, sentence: str) -> None:
    """Replace whatever was last said about the currency with ``sentence``."""
    for stale in suggestion.currency_warnings:
        if stale in suggestion.warnings:
            suggestion.warnings.remove(stale)
    suggestion.currency_warnings = [sentence]
    suggestion.warnings.append(sentence)


def apply_conversion(
    suggestion: InvoiceSuggestion, *, kroner: Decimal, rate: Decimal, quoted_on: date
) -> None:
    """Fill the kroner amount from a published rate, and show the arithmetic.

    Called only for a bilag in a foreign currency, and only once a rate has
    actually been fetched (:mod:`myntos.integrations.norgesbank`). Until now
    such a bilag left the amount field empty and told the user to go and look it
    up, which is correct and useless in equal measure: the ledger really is in
    kroner and the document really does not say how many, but the user was sent
    to a rate table to do a multiplication a computer was standing right there
    to do.

    So the field is filled, and everything needed to disagree with it is put on
    screen next to it: the rate, the day it is quoted for, and the reason the
    figure is probably a little low. The amount is marked
    :attr:`FieldSource.COMPUTED` rather than :attr:`FieldSource.DOCUMENT`,
    because the document does not say it.

    **It stays a proposal, and the wording says why.** What the account was
    charged includes the card issuer's spread and any fee, neither of which is
    knowable from the invoice, so the true figure is on the bank statement.
    Norges Bank's midkurs gets the user to within a percent of it in one click
    instead of leaving them at zero.
    """
    suggestion.gross_amount = kroner
    suggestion.exchange_rate = rate
    suggestion.rate_quoted_on = quoted_on
    suggestion.mark("gross_amount", FieldSource.COMPUTED)
    printed = (
        f"{_written(suggestion.foreign_amount)} {suggestion.currency_code}"
        if suggestion.foreign_amount is not None
        else f"Beløpet i {suggestion.currency_code}"
    )
    _warn_currency(
        suggestion,
        f"Bilaget er i {suggestion.currency_code}. Vi har regnet om {printed} med Norges Banks "
        f"kurs {quoted_on:%d.%m.%Y} ({_written_rate(rate)}) til {_written(kroner)} kr. Sjekk mot "
        "kontoutskriften før du bokfører: kortgebyr og vekslingspåslag kommer i tillegg, og "
        "det er beløpet som faktisk ble belastet som skal føres.",
    )
    note_capitalisation(suggestion)


# skatteloven § 14-40: a driftsmiddel costing 15 000 kroner or more, with an
# economic life of three years or more, is capitalised and depreciated rather
# than expensed in full. The amount half of that test is arithmetic and belongs
# here; the lifetime half is a judgement the user makes, so the sentence asks
# rather than decides.
CAPITALISATION_THRESHOLD = Decimal(15000)
# Categories where a single large purchase plausibly *is* a driftsmiddel. Goods
# for resale, rent and salary are all large by nature and none of them is one.
_CAPITALISABLE = frozenset({"inventar", "datakostnad"})


def note_capitalisation(suggestion: InvoiceSuggestion) -> None:
    """Say so when an expense is large enough to be an asset instead.

    Re-runnable, and re-run after anything that changes either half of the test:
    the amount (a conversion from a foreign currency) or the category (an answer
    from the model). A 2 137,00 GBP Mac Studio is under any threshold until it
    becomes 27 108,49 kroner, which is exactly the case this exists for.
    """
    for stale in suggestion.capitalisation_warnings:
        if stale in suggestion.warnings:
            suggestion.warnings.remove(stale)
    suggestion.capitalisation_warnings = []

    amount = suggestion.gross_amount
    if (
        suggestion.direction is not CashFlowDirection.UT
        or amount is None
        or amount < CAPITALISATION_THRESHOLD
        or (suggestion.type_code is not None and suggestion.type_code not in _CAPITALISABLE)
    ):
        return
    sentence = (
        f"Kjøpet er på {_written(amount)} kr. Varer over 15 000 kroner som skal brukes i tre år "
        "eller mer, føres ikke som kostnad med én gang: de aktiveres som driftsmiddel og "
        "avskrives. Skal dette brukes kortere enn tre år, kan du føre det som kostnad."
    )
    suggestion.capitalisation_warnings = [sentence]
    suggestion.warnings.append(sentence)
