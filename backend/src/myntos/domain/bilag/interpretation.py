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

_INVOICE_DATE_LABELS = ("fakturadato", "faktura dato", "bilagsdato", "invoice date", "utstedt")
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
    "faktura",
    "kreditnota",
)
# Who the document is addressed to, for a sales invoice we issued ourselves.
_BUYER_LABELS = ("kunde", "kjøper", "fakturamottaker", "fakturaadresse", "customer", "bill to")
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
    due_date: date | None = None
    gross_amount: Decimal | None = None
    net_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    # A fraction, snapped to a rate the seeded VAT codes know: 0.25, 0.15, 0.12.
    vat_rate: Decimal | None = None
    invoice_number: str | None = None
    # Every organisasjonsnummer with a valid check digit, in the order printed.
    orgnrs: tuple[str, ...] = ()
    seller_name: str | None = None
    # Only looked for behind a "Kunde"-style label, and only useful when the
    # seller turns out to be us.
    buyer_name: str | None = None
    currency_code: str | None = None
    is_credit_note: bool = False


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


def _dates(lines: list[str], *, today: date) -> tuple[date | None, date | None]:
    """The invoice date and the due date, kept apart.

    The due date is the one date on an invoice that must never become the
    posting date, so lines naming it are excluded from the search for the other.
    Failing an explicit ``Fakturadato``, a line labelled merely ``Dato`` is used,
    and failing that the earliest date left in the document — an invoice is dated
    before it falls due and before the period it covers ends.
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
        return labelled, due
    if generic is not None:
        return generic, due
    return (min(loose) if loose else None), due


def _invoice_number(lines: list[str]) -> str | None:
    for line in lines:
        low = line.lower()
        for label in _NUMBER_LABELS:
            position = low.find(label)
            if position == -1:
                continue
            match = _INVOICE_NUMBER_RE.match(line, position + len(label))
            if match is not None and len(match.group(1)) <= 32:
                return match.group(1)
    return None


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
    if any(word == low or low.startswith(f"{word} ") for word in _TITLE_WORDS):
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

    for line in lines[:8]:
        candidate = _clean_name(line)
        if candidate is not None:
            return candidate
    return None


def _buyer_name(lines: list[str]) -> str | None:
    """Who the document is addressed to.

    Only useful for an invoice we issued ourselves, where the counterparty is the
    customer rather than the sender — but that is exactly the case where the
    sender's name is our own and therefore no help.
    """
    for index, line in enumerate(lines):
        low = line.lower()
        for label in _BUYER_LABELS:
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


def _currency(text: str) -> str | None:
    if _NOK_MARK.search(text) is not None:
        return None
    for code, pattern in _CURRENCIES:
        if pattern.search(text) is not None:
            return code
    return None


def read(text: str, *, today: date) -> InvoiceReading:
    """Extract what ``text`` states, without deciding anything about it."""
    if not text.strip():
        return InvoiceReading()

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

    invoice_date, due_date = _dates(lines, today=today)
    orgnrs = _orgnrs(text)
    low = text.lower()

    return InvoiceReading(
        invoice_date=invoice_date,
        due_date=due_date,
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        vat_rate=Decimal(0) if vat == 0 else _snap_rate(rate),
        invoice_number=_invoice_number(lines),
        orgnrs=orgnrs,
        seller_name=_seller_name(lines, orgnrs[0] if orgnrs else None),
        buyer_name=_buyer_name(lines),
        currency_code=_currency(text),
        is_credit_note="kreditnota" in low or "credit note" in low,
    )


# --- From what it says to what the form should hold -------------------------------


@dataclass(frozen=True)
class PartyHint:
    """One registered customer or supplier, as far as matching needs to know."""

    id: int
    name: str
    orgnr: str | None
    is_customer: bool


class TypeSource(enum.StrEnum):
    """Where the proposed category came from.

    Shown to the user rather than kept for statistics: "we recognised the word
    nettleie" and "a model picked this" are different degrees of confidence, and
    the person about to post the entry is entitled to know which one they are
    looking at.
    """

    DOCUMENT = "dokument"
    MODEL = "ki"


@dataclass
class InvoiceSuggestion:
    """Proposed form values. Every field is optional, and the user confirms."""

    direction: CashFlowDirection = CashFlowDirection.UT
    type_code: str | None = None
    type_source: TypeSource | None = None
    tax_code: str | None = None
    occurred_on: date | None = None
    gross_amount: Decimal | None = None
    vat_amount: Decimal | None = None
    description: str | None = None
    party_id: int | None = None
    counterparty_name: str | None = None
    counterparty_orgnr: str | None = None
    invoice_number: str | None = None
    currency_code: str | None = None
    # Which fields were read off the document, so the UI can say which ones they
    # are instead of presenting guesses as if the user had typed them.
    filled: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The subset of `warnings` that belongs to the current category. Kept apart
    # so that replacing the category (apply_type) takes the previous one's advice
    # with it instead of leaving contradictory sentences behind.
    type_warnings: list[str] = field(default_factory=list)


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
    source: TypeSource | None,
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
    suggestion.filled = [
        name for name in suggestion.filled if name not in {"type_code", "tax_code"}
    ]
    suggestion.type_code = type_code
    suggestion.type_source = source if type_code is not None else None
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
        suggestion.filled.append("type_code")
    else:
        warn(
            "Vi fant ikke ut hva dette gjelder. Velg type selv — den bestemmer hvilken konto "
            "beløpet havner på, og dermed hva Skatteetaten får se."
        )

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
        suggestion.filled.append("tax_code")
        if default is not None and default != code:
            warn(
                f"Fakturaen viser {int(rate * 100)} % mva, så mva-koden er satt etter "
                "fakturaen og ikke etter typen."
            )
    elif reading.vat_amount == 0:
        warn(
            "Fakturaen ser ut til å være uten mva. Kontroller mva-koden — kjøp fra utlandet "
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
) -> InvoiceSuggestion:
    """Propose form values for the invoice in ``text``.

    ``own_orgnr`` and ``own_name`` identify the company being booked for, and are
    what makes the direction guessable at all: a document naming us before it
    names anyone else is one we issued, and therefore money in. Everything else
    is treated as a purchase, which is what the large majority of uploaded
    invoices are.
    """
    hints = parties or []
    found = reading if reading is not None else read(text, today=today)
    suggestion = InvoiceSuggestion(
        invoice_number=found.invoice_number, currency_code=found.currency_code
    )

    if not text.strip():
        suggestion.warnings.append(
            "Vi fant ingen tekst i PDF-en. Er den skannet eller fotografert, må du fylle "
            "ut feltene selv — dokumentet blir lagret som bilag uansett."
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
        suggestion.filled += ["direction", "party_id"]
    elif we_issued_it:
        suggestion.direction = CashFlowDirection.INN
        suggestion.filled.append("direction")

    if suggestion.counterparty_name is None:
        suggestion.counterparty_name = other_name
    suggestion.counterparty_orgnr = counterparty_orgnr

    # --- Date ---
    if found.invoice_date is not None:
        suggestion.occurred_on = found.invoice_date
        suggestion.filled.append("occurred_on")
        if found.invoice_date > today:
            suggestion.warnings.append(
                "Datoen vi fant ligger fram i tid. Bokfør på datoen betalingen faktisk skjedde."
            )
    else:
        suggestion.warnings.append("Vi fant ingen fakturadato — velg dato selv.")

    # --- Amount ---
    if found.gross_amount is not None:
        suggestion.gross_amount = found.gross_amount
        suggestion.vat_amount = found.vat_amount
        suggestion.filled.append("gross_amount")
        if (
            found.net_amount is not None
            and found.vat_amount is not None
            and abs(found.gross_amount - (found.net_amount + found.vat_amount)) > 1
        ):
            suggestion.warnings.append(
                "Beløpene på fakturaen summerer seg ikke helt — kontroller totalen."
            )
    else:
        suggestion.warnings.append("Vi fant ikke totalbeløpet — skriv det inn selv.")

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
        source=TypeSource.DOCUMENT if heuristic is not None else None,
    )

    # --- Description ---
    parts: list[str] = []
    if found.invoice_number is not None:
        kind = "Kreditnota" if found.is_credit_note else "Faktura"
        parts.append(f"{kind} {found.invoice_number}")
    if suggestion.counterparty_name is not None:
        parts.append(suggestion.counterparty_name)
    if parts:
        suggestion.description = " — ".join(parts)[:255]
        suggestion.filled.append("description")

    # --- Things worth saying out loud ---
    if found.currency_code is not None:
        suggestion.warnings.append(
            f"Fakturaen ser ut til å være i {found.currency_code}. Hovedboken føres i kroner: "
            "skriv inn beløpet som faktisk ble belastet kontoen."
        )
    if found.is_credit_note:
        suggestion.warnings.append(
            "Dette ser ut som en kreditnota. Den føres i motsatt retning av fakturaen den gjelder."
        )
    return suggestion
