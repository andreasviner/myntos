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


#: A minus immediately in front of a figure, hyphen or either dash. "NOK
#: -27 850,81" on a bank statement, with the currency word in between.
_MINUS_BEFORE = re.compile(r"[-\u2212\u2013]\s*$")


def signed_amounts_in(line: str) -> list[tuple[Decimal, bool]]:
    """Every amount on one line, with whether a minus was printed in front.

    The sign is the one thing a bank statement says about direction and the one
    thing an invoice never says at all. An invoice for 12 500 and a card
    purchase of 12 500 are the same figure; "NOK -12 500,00" is not, and
    dropping the minus threw away the difference between money leaving and money
    coming back.
    """
    found: list[tuple[Decimal, bool]] = []
    numbers = _number_spans(line)
    for match in _AMOUNT_RE.finditer(line):
        if _PERCENT_AFTER.match(line, match.end()) is not None:
            continue
        if _inside(match, numbers):
            continue
        value = parse_amount(match.group())
        if value is not None:
            found.append((value, _MINUS_BEFORE.search(line[: match.start()]) is not None))
    return found


def amounts_in(line: str) -> list[Decimal]:
    """Every amount on one line, in order, skipping VAT rates.

    A figure followed by a percent sign is a rate, not a sum. Dropping those here
    is what lets ``Mva 25 % 312,50`` be read for its 312,50.
    """
    found: list[Decimal] = []
    numbers = _number_spans(line)
    for match in _AMOUNT_RE.finditer(line):
        if _PERCENT_AFTER.match(line, match.end()) is not None:
            continue
        if _inside(match, numbers):
            continue
        value = parse_amount(match.group())
        if value is not None:
            found.append(value)
    return found


def _number_spans(line: str) -> list[tuple[int, int]]:
    """Where on the line a number is printed that is never an amount."""
    return _kontonummer_spans(line) + _registration_spans(line)


def _registration_spans(line: str) -> list[tuple[int, int]]:
    """Where on the line an organisasjonsnummer is printed with «MVA» behind it.

    That is how the law has a seller print its number (bokføringsforskriften
    § 5-1-1 nr. 2), and it is never an amount. Spaced, it reads as one: Fatland's
    slakteavregning heads its letterhead «Foretagsreg: NO 913 251 342 MVA», the
    word «MVA» made it a VAT line, and its slaughter came back with 913 251 342
    kroner of VAT. «Foretaksregisteret» had been kept off VAT lines by name, and
    a buyer that spells the register differently got through. Recognised by the
    shape and the check digit, so the next spelling does too.
    """
    spans: list[tuple[int, int]] = []
    for match in _REGISTRATION.finditer(line):
        try:
            validate_orgnr(re.sub(r"\D", "", match.group()))
        except InvalidOrgnrError:
            continue
        spans.append(match.span())
    return spans


def _kontonummer_spans(line: str) -> list[tuple[int, int]]:
    """Where on the line a bank account number is printed.

    A kontonummer is never an amount, and the spaced form reads as three of
    them: "NOK 15.000,00 betales til kontonummer 1503 84 20000" is how Coop writes its whole
    payment line, and it came back as an invoice of 20 000 kroner, because the
    total is the *last* figure on a "betales" line and the account's last group
    of five digits is a figure. The dotted form never matched, which is why the
    rule held as long as every invoice printed the account in front of the
    amount or with dots.
    """
    return [match.span() for match in _KONTONUMMER.finditer(line)]


def _inside(match: re.Match[str], spans: list[tuple[int, int]]) -> bool:
    return any(start <= match.start() and match.end() <= end for start, end in spans)


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
    (
        "å betale",
        "a betale",
        "til betaling",
        # "Betales til bankkonto 4202.11.39496 NOK 1 440,00" is the whole total
        # line on an invoice that labels nothing else — the item table's last
        # column is the gross and carries no word at all. The last figure on the
        # line is taken (see _labelled_amount), which is the amount and not the
        # kontonummer in front of it.
        "betales til",
        "betales",
        # A settlement the buyer wrote says what it paid rather than what is
        # owed: «Godskrives, overført til bankkonto: <kontonummer> <beløp>»
        # on Q-Meieriene's avregningsoppgave, under two «Beløp inkl. mva.»
        # lines, the sale's and the deductions'. Without these the larger of
        # those two was the total, which is the sale before the deductions and
        # not what reached the bank. «Utbetalt» is left out on purpose: the same
        # documents print «utbetalt hittil i år», which is the year so far.
        "godskrives",
        "til utbetaling",
        # What an AliExpress VOEC invoice calls the gross, in front of the
        # foreign figure and the kroner one. Without it the bare "Total" below
        # wins instead, and on those invoices "Total" heads the VAT summary:
        # "Total 425 106" is a base of 425 and VAT of 106, which the
        # thousands-separator reading takes for 425 106 kroner.
        "amount paid",
        "amount due",
        "total due",
        "balance due",
    ),
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
    # The bare words, and the one a bank app uses. A payment screenshot is a
    # bilag too, and it says "Amount NOK 7 500,00" where an invoice would say
    # "Å betale" — without this the amount on every FINN purchase paid by
    # transfer had to be typed in by hand.
    ("totalt", "total", "sum", "beløp", "amount"),
)
# Lines stating a figure that is not the gross total, whatever they are called.
# "Original amount GBP 2 137,00" is what a bank prints above the exchange rate:
# the same payment in the currency it was charged in, not a second payment. Left
# in, it counted twice on any card purchase abroad.
_NOT_A_TOTAL = (
    "eks",
    "ekskl",
    "netto",
    "avrunding",
    "restbeløp",
    "purregebyr",
    "grunnlag",
    "original",
    # "Subtotal" contains "total", and is larger than the total whenever there
    # was a discount: AliExpress prints "Subtotal: 5 939 kr" over "Total:
    # 5 908,81 kr", and the largest labelled figure was taken.
    "subtotal",
)

#: A Norwegian bank account number as it is printed, "1520.34.91244". Four
#: digits, two, five, separated by dots or by spaces: the dots are how a
#: statement writes it, and a bank app writes "4214 33 41180". Missing the
#: spaced form meant the one screenshot that said what the card was charged
#: was not recognised as the bank, and nine AliExpress orders were read as
#: if they were nine separate claims on the account.
_KONTONUMMER = re.compile(r"\b\d{4}([. ])\d{2}\1\d{5}\b")

#: An organisasjonsnummer followed by «MVA», as a seller prints it. See
#: _registration_spans.
_REGISTRATION = re.compile(r"(?i)(?<!\d)(?:NO\s?)?\d{3}([ .]?)\d{3}\1\d{3}\s*MVA\b")

#: The heading `myntos.api.v1.cashflow` writes above each file when a bilag has
#: more than one. Known here because the total is worked out per document and
#: then added up, and this is what says where one document ends.
DOCUMENT_HEADING_PREFIX = "--- Dokument "
_DOCUMENT_HEADING = re.compile(r"^-{3}\s*Dokument\s+\d+\s*:")

_NET_LABELS = ("eks mva", "eks. mva", "ekskl mva", "ekskl. mva", "netto", "subtotal", "grunnlag")

# "avgift" is deliberately absent, and its absence is load-bearing. It is the
# ordinary Norwegian word for a fee: "Avgift for Trygg betaling 206,00 kr" on a
# FINN receipt was read as 206 kroner of VAT on a 3 565-krone purchase from a
# private seller, which then set the deduction code on the whole entry. Every
# document that really does state VAT states it as "mva", "merverdiavgift",
# "moms" or "vat", and "merverdiavgift" is listed in its own right.
_VAT_LABELS = ("herav mva", "sum mva", "mva", "merverdiavgift", "moms", "vat")
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
    # The settlement's own date, the day the buyer closed the period: the sale
    # belongs to that day and that year, whenever the money follows. A
    # December melkeoppgjør is paid in January and is the year before's income.
    "avregningsdato",
    "oppgjørsdato",
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
# «Utbetalingsdato» is when the buyer of a settlement will pay, which makes it
# the settlement's due date and not its date. It has to be named here, because
# it contains «betalingsdato», which is a receipt's own date (the day the money
# moved): matched as that, Q-Meieriene's September milk was dated 9 October.
_DUE_LABELS = (
    "forfall",
    "betalingsfrist",
    "betales innen",
    "utbetalingsdato",
    "due date",
    "payment due",
)
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
    "avregningsnummer",
    "avregningsnr",
    "avregning nr",
    "oppgjørsnummer",
    "oppgjørsnr",
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
# The labels that number a settlement the buyer wrote. Named as what it is for
# the same reason: Fatland's «Avregning nr. 02163961» is the farm's own sales
# document, and the ledger calling it «Faktura 02163961» would say the farm
# invoiced the slaughterhouse.
_SETTLEMENT_LABELS = frozenset(
    {"avregningsnummer", "avregningsnr", "avregning nr", "oppgjørsnummer", "oppgjørsnr"}
)
# Who the document is addressed to, for a sales invoice we issued ourselves.
_BUYER_LABELS = ("kunde", "kjøper", "fakturamottaker", "fakturaadresse", "customer", "bill to")
# Who sent it, said outright. A webshop receipt has no organisasjonsnummer to
# anchor the name to, but it does say "Sold by" or "Items bought from".
_SELLER_LABELS = (
    # Coop's samlefaktura heads its letterhead «Fakturaavsender» and puts the
    # name on the next line.
    "fakturaavsender",
    "selger",
    "solgt av",
    "levert av",
    "utstedt av",
    "leverandør",
    "sold by",
    "sold and shipped by",
    "bought from",
    "seller",
    # Who the payment goes to, in the bank-details block a foreign invoice
    # prints. It is the seller said as plainly as "Sold by", and on MA-3D's
    # pro forma invoice it is the only statement of it: Løynd's transcription
    # puts that block first and the buyer's address second, with the letterhead
    # at the very bottom, so the top-of-page guess found nothing and the
    # filename, "PI Andreas Lindeman _26-0023", named the buyer as the seller.
    # Only the "name" form: a bare "Beneficiary" on a bank screenshot of money
    # coming in is us.
    "beneficiary's name",
    "beneficiary´s name",  # noqa: RUF001
    "beneficiary’s name",  # noqa: RUF001
    "beneficiary name",
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
# Six, not eight, because "Batteri" is seven characters and is the whole item
# line of a real invoice. The letters test in _clean_item is what keeps junk
# out; this only rules out a stub too short to be a word at all.
_ITEM_MIN_CHARS = 6
_ITEM_MAX_CHARS = 140
# "800545236002", "ART-4471/A": an identifier and nothing else.
_ARTICLE_ONLY = re.compile(r"^[\W\d]*[A-Za-z]{0,4}[-/ ]?\d[\d\-/ ]*[A-Za-z]?[\W\d]*$")
# One figure column at the end of a row: a quantity, a rate, or a price with or
# without its currency. Applied repeatedly, because a row carries as many of
# these as the table has columns — "Batteri 1 1 152,00 1 152,00 288,00 1 440,00"
# has five after the name, and stripping only the last leaves a line that is
# mostly digits and gets thrown out for being mostly digits.
_TRAILING_FIGURE = re.compile(
    r"(?i)\s*(?:kr|nok|eur|usd|gbp|sek|dkk|[€$£])?\s*"
    r"\d{1,3}(?:[\s., ]\d{3})*(?:[.,]\d{1,2})?\s*(?:%|kr|nok|eur|usd|gbp|sek|dkk|[€$£])?\s*$"  # noqa: RUF001
)
#: How many figure columns may be taken off one row. A table is wide; a line
#: that keeps shrinking under that pattern was never the name of a thing.
_MAX_FIGURE_COLUMNS = 8
#: The tail of a field label, once the value beside it has been stripped away.
_LABEL_SUFFIX = re.compile(r"(?i)(?:dato|nummer|nr|kode)$")
#: How far under an item heading the first item may sit. Every candidate still
#: has to pass _clean_item, so the cost of looking further is a column header or
#: a totals row being considered and refused, not one being believed.
_ITEM_SEARCH_LINES = 12

_RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%")
# The same separator twice or none at all, because that is how an orgnr is
# printed: «913 251 342», «913.251.342», «913251342». Either separator on its own
# also read the first two columns of a slaughter settlement, varenr «162060» and
# skrottnr «341», as one number, and two carcasses in seven passed the check
# digit. A bilag whose only real orgnr is the reader's own was then no longer
# one that names only us.
_ORGNR_RE = re.compile(r"(?i)(?<!\d)(?:NO\s?)?(\d{3})([ .]?)(\d{3})\2(\d{3})(?!\d)")
# The separator between the label and the number. The full stop is in the class
# because Norwegian invoices abbreviate: "Fakturanr.: 1075" left the parser
# looking at ".: 1075", finding nothing, and falling through to the next label
# in the list — which is "ordrenummer", so a faktura came back numbered by its
# order line instead.
_INVOICE_NUMBER_RE = re.compile(r"[:.\s#]*([A-Za-z0-9][A-Za-z0-9\-/]*\d[A-Za-z0-9\-/]*)(?![./-]\d)")
_LETTER = re.compile(r"[A-Za-zÆØÅæøå]")
# A label in front of the name, stripped so that "Selger: Anders A" is the name
# and not the label with the name stuck to it. The word boundary is what makes
# it a label rather than a prefix: without it "Selgeren har sendt pakken", a
# status line on FINN, has its first six letters taken off and arrives as the
# counterparty "en har sendt pakken".
_NAME_PREFIX = re.compile(r"(?i)^(?:fra|selger|leverandør|utstedt av)\b\s*[:\-]?\s*")
# A street or postal line, which is what sits between a sender's name and its
# organisasjonsnummer and would otherwise be mistaken for the name.
# «Bedriftsvn 21» is Q-Meieriene's street: «vn» is how a footer abbreviates
# «veien», and «veg»/«vegen» is the nynorsk spelling half of Rogaland uses.
_ADDRESS_LINE = re.compile(
    r"(?i)\b\d{4}\b|\bpostboks\b|\bp\.?b\.?\s*\d|"
    r"(?:vei|veien|vn|veg|vegen|gate|gaten|gata|gt|allé|alle|plass|torg|brygge)\s*\.?\s*\d"
)

# The rates the seeded VAT codes know about, as fractions.
_KNOWN_RATES = (Decimal("0.25"), Decimal("0.15"), Decimal("0.12"))
#: The tollpass codes: the whole amount is the import VAT, paid on the freight
#: forwarder's or Tolletaten's bill. A bilag in foreign currency filed under
#: them is that bill, and its own code is the right one.
_IMPORT_VAT_CODES = frozenset({"14", "15"})
#: A seller in VOEC, the simplified scheme for foreign web shops, has to print
#: its VOEC number on what it sends ("VOEC ID: 2034638" on AliExpress). The word
#: appears on nothing else a business is billed with, so it is read as the
#: statement it is rather than inferred from the seller's name or the rate.
_VOEC = re.compile(r"(?i)\bvoec\b")

_TAX_CODE_BY_RATE: dict[CashFlowDirection, dict[Decimal, str]] = {
    CashFlowDirection.UT: {Decimal("0.25"): "1", Decimal("0.15"): "11", Decimal("0.12"): "13"},
    CashFlowDirection.INN: {Decimal("0.25"): "3", Decimal("0.15"): "31", Decimal("0.12"): "33"},
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
        # "bilverksted", not "verksted". A verksted is as often a company's own
        # workshop or a supplier's — "Faktura fra Elektra Verksted", "Avdeling:
        # 04 Verksted" — as it is a garage, and on that invoice the word turned
        # a 1 440-krone battery into a motoring cost before any model was asked.
        # A keyword list is allowed to be crude; it is not allowed to be crude
        # in a way that decides an account on a word this ambiguous.
        "bilkostnad",
        ("drivstoff", "bensin", "diesel", "bompenger", "bomring", "dekkskift", "bilverksted"),
    ),
    ("reise", ("flybillett", "overnatting", "hotellopphold", "hotell", "togbillett", "leiebil")),
    ("kontorrekvisita", ("kontorrekvisita", "rekvisita", "toner", "blekkpatron", "kopipapir")),
    ("bankgebyr", ("kortgebyr", "bankgebyr", "transaksjonsgebyr")),
    ("inventar", ("verktøy", "inventar", "møbler", "kontorstol")),
    ("varekjop", ("varekjøp", "varelinjer", "engros", "videresalg")),
)

# «Avregning» is how a slaughterhouse heads the settlement it writes for the
# farmer («Avregning nr. 02163961»), and it sits at the top of the page where a
# letterhead would.
_TITLE_WORDS = (
    "faktura",
    "kreditnota",
    "invoice",
    "kvittering",
    "purring",
    "betalingsvarsel",
    "avregning",
    "avregningsoppgave",
)
# Field labels, which are what is left on a line once the value beside them has
# been stripped off. "Org.nr: NO 991 107 711 MVA" minus the number is "Org.nr",
# and "Kundenummer 4595670" split at the label is "nummer" — both of which used
# to come back as the counterparty's name and be shown to the user as one.
#
# Matched exactly, on a form with the punctuation and spacing taken out, rather
# than as a prefix: a real business may perfectly well be called "Referanse AS"
# or "Kontakt Norge", and a prefix rule would refuse it.
_LABEL_WORDS = frozenset(
    (
        "levering",
        "leveringsdato2",
        "frakt",
        "betaling",
        "status",
        "selger",
        "kjøper",
        "kjoper",
        "detaljer",
        "details",
        "sammendrag",
        "summary",
        "deresref",
        "vårref",
        "varref",
        "vaarref",
        "ref",
        "referanse",
        "orgnr",
        "organisasjonsnr",
        "organisasjonsnummer",
        "foretaksnavn",
        # The register a seller is in, without the number that followed it, in
        # the spellings the pilot's paperwork has used. «Foretaksregisteret»
        # itself is in NOT_A_PARTY.
        "foretaksreg",
        "foretagsreg",
        "foretagsregisteret",
        "nummer",
        "kundenummer",
        "kundenr",
        "fakturanummer",
        "fakturanr",
        "ordrenummer",
        "ordrenr",
        "kontonr",
        "kontonummer",
        "bankkonto",
        "epost",
        "email",
        "telefon",
        "mobil",
        "avdeling",
        "vårkontakt",
        "varkontakt",
        "vaarkontakt",
        "kontakt",
        "leveringsdato",
        "leveringsadresse",
        "fakturadato",
        "forfallsdato",
        "side",
        "dato",
        "beløp",
        "belop",
        "antall",
        "enhet",
        "pris",
    )
)
#: Longest a company name may be, in words. See _clean_name.
_MAX_NAME_WORDS = 6

# Everything that is not a letter or a digit, for the comparison above.
_NOT_ALNUM = re.compile(r"[^0-9a-zæøåA-ZÆØÅ]+")
# "F A K T U R A" — a heading set with letter spacing, which a PDF text layer
# hands over as single-character words. It is a word to a reader and a row of
# letters to a parser, and it used to end up in the ledger as what the payment
# was for.
_LETTERSPACED = re.compile(r"(?i)^(?:[a-zæøå0-9][\s.]+){3,}[a-zæøå0-9][\s.]*$")
# The contents of an invoice footer, which is where the sender's
# organisasjonsnummer often is — and therefore where the search for the sender's
# name lands. "Bank: Nordea Bank, SWIFT: NDEANOKK, IBAN: NO59 63450530699,
# Organisasjonsnr" is what that line looks like with the number taken off it: it
# is mostly letters, it is the right length, and it is not a company name.
_NOT_A_NAME = re.compile(
    r"(?i)\b(?:iban|swift|bic|bank|kontonr|kontonummer|telefon|tlf|e-post|epost|"
    r"e-mail|faks|fax|www)\b|@"
)
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
    # ``None`` when the document printed *several* rates, which is a different
    # thing from printing none and is why `printed_rates` exists beside it.
    vat_rate: Decimal | None = None
    # Every distinct rate the document states. A bilag stating more than one
    # cannot be a single entry with a single code, and the user is told so
    # rather than handed whichever rate was printed first.
    printed_rates: tuple[Decimal, ...] = ()
    invoice_number: str | None = None
    # What the number is called on the document: ``None`` for a faktura, "Ordre"
    # for an order or a receipt. Only ever used to word the description.
    number_kind: str | None = None
    # Every organisasjonsnummer with a valid check digit, in the order printed.
    orgnrs: tuple[str, ...] = ()
    seller_name: str | None = None
    # Whether ``seller_name`` is nothing but the file's title. A title is the
    # weakest thing a name can be read from, because it is often the filename,
    # and a filename is whatever the person who saved it typed: an invoice
    # saved as "PI Andreas Lindeman _26-0023" names the buyer. So it fills the
    # field only until the model has read the page, and the model's MOTPART
    # replaces it. See :func:`myntos.domain.bilag.extraction.merge`.
    seller_named_by_title: bool = False
    # Only looked for behind a "Kunde"-style label, and only useful when the
    # seller turns out to be us.
    buyer_name: str | None = None
    currency_code: str | None = None
    is_credit_note: bool = False
    #: Whether any document in the bilag is a VOEC invoice: VAT charged to a
    #: consumer by a foreign web shop, which no business may deduct. See
    #: :func:`apply_type`.
    under_voec: bool = False
    #: Whether the total was printed with a minus in front, and ``None`` when no
    #: total on the page carried a sign. An invoice never signs its total: it
    #: states what is owed and leaves the direction to whoever reads it. A bank
    #: statement signs everything, and that sign is the only thing on the page
    #: that says which way the money went.
    total_is_negative: bool | None = None
    #: Both signs on the same page: a purchase and its refund, a charge and its
    #: reversal. See :func:`_shows_a_return`.
    shows_a_return: bool = False
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


#: Labels this short are found inside ordinary words: "vat" in "Private
#: Limited", "sum" in "Summary". Longer ones are left as substrings, because
#: Norwegian compounds them and "Totalbeløp" has to keep finding "beløp".
_WHOLE_WORD_LABEL = 3


def _has_label(low: str, labels: tuple[str, ...]) -> bool:
    """Whether the lowercased line carries one of ``labels``.

    "Alibaba.com Singapore E-Commerce Private Limited is raising this invoice
    ... by 3rd party offshore sellers" was read as VAT of 3 kroner, because
    "private" contains "vat" and "3rd" ends in a figure.
    """
    for label in labels:
        if len(label) <= _WHOLE_WORD_LABEL:
            if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", low):
                return True
        elif label in low:
            return True
    return False


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
    candidates = _labelled_amounts(lines, labels, exclude=exclude, allow_zero=allow_zero)
    return max((value for value, _negative in candidates), default=None)


def _labelled_amounts(
    lines: list[str],
    labels: tuple[str, ...],
    *,
    exclude: tuple[str, ...] = (),
    allow_zero: bool = False,
) -> list[tuple[Decimal, bool]]:
    """Every amount belonging to one of ``labels``, in the order they appear.

    Each carries whether a minus was printed in front of it. Order is kept
    because a document that signs its amounts is a statement rather than an
    invoice, and on a statement the largest figure is not the point: three
    screenshots of an eBay refund show the purchase of 27 850,81 and the refund
    of 26 266,40, and the bilag is about whichever one the user led with.
    """
    found_all: list[tuple[Decimal, bool]] = []
    for index, line in enumerate(lines):
        low = line.lower()
        if not _has_label(low, labels):
            continue
        if any(word in low for word in exclude):
            continue
        found = signed_amounts_in(line)
        pair = found[-1] if found else None
        if pair is None:
            # Only across blank lines. Scanning past a line with words on it is
            # how "Mva. 25 %" on a FINN receipt reached down two lines, over
            # "Avgift for Trygg betaling", and claimed the 206,00 kroner
            # underneath it as VAT on a purchase from a private seller.
            for following in lines[index + 1 : index + 3]:
                if not following.strip():
                    continue
                if _is_bare_amount(following):
                    pair = signed_amounts_in(following)[-1]
                break
        if pair is None:
            continue
        value, negative = pair
        if value == 0 and not allow_zero:
            continue
        found_all.append((value, negative))
    return found_all


#: The two halves of an amount a narrow table cell wrapped at its thousands
#: space: «Total 255» over «835,31». The tail is exactly one group of three
#: and the øre, which is what is left when a cell breaks there.
_WRAPPED_HEAD = re.compile(r"(?:^|\s)\d{1,3}$")
_WRAPPED_TAIL = re.compile(r"^\d{3}[.,]\d{2}$")


def _rejoin_wrapped_amounts(lines: list[str]) -> list[str]:
    """The lines with every amount a cell wrapped put back on one line.

    Fatland's slakteavregning prints its sum, VAT and total in a column too
    narrow for six figures, and the text layer hands them over as «Sum brutto
    204», «668,25», «Moms 51 167,06», «Total 255», «835,31». The label's last
    figure is the one taken (_labelled_amounts), so a slaughter of 255 835,31
    kroner came back as 255. The line below a label is only read when it is a
    figure alone, which «835,31» is, but only for a label with no figure of its
    own, which «Total 255» is not.
    """
    joined: list[str] = []
    for line in lines:
        if (
            joined
            and _WRAPPED_TAIL.match(line) is not None
            and _WRAPPED_HEAD.search(joined[-1]) is not None
        ):
            joined[-1] = f"{joined[-1]} {line}"
            continue
        joined.append(line)
    return joined


def _shows_a_return(totals: dict[Decimal, bool | None]) -> bool:
    """Whether the bilag shows money going out *and* money coming back.

    Both directions on the same bilag is not an ambiguity, it is a fact about
    what happened: a purchase and its refund, a charge and its reversal. It is
    worth knowing separately from the total, because it changes what the money
    coming in *is*. Money that came back is not money that was earned, and a
    return filed as a sale adds turnover the company never had and output VAT it
    does not owe.

    Read off the resolved totals rather than off the lines, so that the same
    payment seen from the bank and from the shop is not mistaken for two.
    """
    if all(negative is None for negative in totals.values()):
        return False
    return {bool(negative) for negative in totals.values()} == {True, False}


def _in_each_document(lines: list[str]) -> list[list[str]]:
    """The lines grouped by the file they came from.

    One group when the bilag is one file, which is every invoice: the text then
    goes through the rules below exactly as it did before there was a group at
    all.
    """
    groups: list[list[str]] = [[]]
    for line in lines:
        if _DOCUMENT_HEADING.match(line) is not None:
            groups.append([])
            continue
        groups[-1].append(line)
    return [group for group in groups if any(line.strip() for line in group)]


def _document_total(lines: list[str]) -> tuple[Decimal | None, bool | None]:
    """What one file says its total is, and whether it printed a minus.

    The largest of the labelled figures, because a document restates its total:
    an invoice prints subtotals above it and a bank screenshot prints the same
    payment twice, once large at the top and once in a details table.
    """
    for labels in _TOTAL_LABELS:
        candidates = _labelled_amounts(lines, labels, exclude=_NOT_A_TOTAL)
        if not candidates:
            continue
        value, negative = max(candidates, key=lambda pair: pair[0])
        return value, negative if any(each for _, each in candidates) else None
    return None, None


#: A sign as the first thing on a line: the minus or plus a bank app prints in
#: front of a transaction's amount.
_LEADING_SIGN = re.compile(r"^\s*([-+\u2212\u2013])")


def _signed_bare_amount(lines: list[str]) -> tuple[Decimal | None, bool | None]:
    """The first line on a bank document that is a signed figure and nothing else.

    A bank app does not label the amount of a transaction. It prints it large,
    "-9 771,76 NOK", under "Outgoing" and over the merchant's name, and that line
    is the only one on the screen that says what the account was charged. Read
    only on a document already recognised as the bank's: on an invoice a line
    that is nothing but a signed figure is a discount or a rounding, not the
    total.
    """
    for line in lines:
        sign = _LEADING_SIGN.match(line)
        if sign is None or not _is_bare_amount(line):
            continue
        found = amounts_in(line)
        if found and found[0] != 0:
            return found[0], sign.group(1) != "+"
    return None, None


def _document_totals(lines: list[str]) -> dict[Decimal, bool | None]:
    """Each distinct total on the bilag, and which way it went.

    Keyed by magnitude, not by magnitude *and* sign. The same payment seen from
    two sides has two signs and is still one payment: the bank writes the
    AliExpress charge as "-600,62" and AliExpress writes it as "Total for 1
    items: 600,62 kr", and telling those apart by sign made them cancel out.

    The bank's sign wins where the two disagree, because the bank is the one
    saying which way the money went. A shop's page only says what something
    cost. ``None`` means no file on the bilag printed a sign for that figure at
    all, which is every invoice ever written.
    """
    groups = _in_each_document(lines)
    # The bank is what says what moved. Everything else on a bilag describes the
    # same money at a different granularity, and adding those together counts it
    # twice: a FINN receipt's 315 kroner of fees is inside the 3 565 the account
    # was charged, and an AliExpress order of 63,09 is inside a card charge of
    # 1 011,44 that bundled two orders. Summing all four documents on the FINN
    # bilag gave 3 880, which is 3 565 plus a part of itself.
    #
    # So when any file on the bilag prints a kontonummer, those are the files
    # that count and the rest are corroboration. With no bank document at all
    # this is every file, which is the single invoice case unchanged.
    banked = [group for group in groups if any(_KONTONUMMER.search(line) for line in group)]

    def totals(
        chosen: list[list[str]], *, at_least: Decimal | None = None
    ) -> dict[Decimal, bool | None]:
        sign_of: dict[Decimal, bool | None] = {}
        for group in chosen:
            value, negative = _document_total(group)
            if value is None and at_least is not None:
                value, negative = _signed_bare_amount(group)
                # An unlabelled figure is believed as the charge only when it
                # could be the charge for the documents beside it. Nine
                # AliExpress orders adding up to 9 771 are inside a bank line of
                # 9 771,76; the FINN bilag's bank line is the owner's 3 000,
                # a different movement from the 3 565 purchase, and smaller
                # than the receipt it sits next to.
                if value is not None and value < at_least:
                    value, negative = None, None
            if value is None:
                continue
            if value not in sign_of or (sign_of[value] is None and negative is not None):
                sign_of[value] = negative
        return sign_of

    others = [group for group in groups if group not in banked]
    banked_totals = totals(banked, at_least=max(totals(others), default=Decimal(0)))
    if banked_totals:
        return banked_totals

    # Nothing said what moved, so nothing here is independent of anything else,
    # and the files are one payment written at different granularities: the FINN
    # bilag prints "Totalt 3 565 kr" on the purchase and "Totalt 315,00 kr" on
    # the fee receipt, and the 315 is inside the 3 565. The largest is the one
    # that contains the others, which is the rule a single invoice has always
    # been read by.
    #
    # This is the case the bank screenshot on that bilag falls into: it is the
    # owner's 3 000 going the other way and states no total at all, so narrowing
    # to it left nothing to add up.
    every = totals(groups)
    if not every:
        return {}
    largest = max(every)
    return {largest: every[largest]}


def _total(lines: list[str]) -> tuple[Decimal | None, bool | None]:
    """The bilag's total, and whether it came out negative.

    **Added up, not picked.** A bilag is every file on it, so nine screenshots
    of an AliExpress order that charged the card three times are one voucher of
    1 758,29 and not whichever of 146,23, 1 011,44 and 600,62 a rule happened to
    prefer. Largest and first were both arbitrary in exactly the same way: they
    answered "which of these is the bilag" when the answer is "all of them".

    **Distinct totals only.** The same figure from two files is one payment
    documented twice, which is what a bilag usually is: the FINN advert and the
    bank line both say 7 500,00, and adding them would invent a purchase that
    never happened. Two genuinely equal charges do collapse into one, and that
    is the safer way to be wrong, since the user can see the amount and the
    documents are right there.

    The sign is kept through the addition. ``None`` when no file printed one,
    which is every invoice ever written: an invoice states what is owed and
    leaves the direction to whoever reads it.
    """
    sign_of = _document_totals(lines)
    if not sign_of:
        return None, None

    signed = Decimal(0)
    for value, negative in sign_of.items():
        signed += -value if negative else value
    if all(negative is None for negative in sign_of.values()):
        return signed, None
    return abs(signed), signed < 0


#: A line that is nothing but a percentage, brackets allowed: "(25 %)".
_BARE_RATE = re.compile(r"^[\s(\[]*\d{1,2}(?:[.,]\d)?\s*%[\s)\]]*$")

#: A row whose last column is a percentage. The text layer often glues it to the
#: amount before it, so no separator is required in front of the figure.
_ROW_RATE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%\s*$")

#: A table heading whose last column is the VAT one, "IMVA" and "Mva." included,
#: and "Mva.%", which says the cells below are rates printed without the sign.
_MVA_LAST_COLUMN = re.compile(r"(?i)\S*(?:mva|moms|vat)[.:]?\s*%?\s*$")
_PERCENT_HEADING = re.compile(r"%\s*$")

#: A row ending in a bare rate, for a heading that carries the "%" itself:
#: Coop's «Netto Mva. Totalt Mva.%» over «… 15.000,00 25». Only the rates Norway
#: has, so that a quantity or a page number in the last column is not one.
_ROW_BARE_RATE = re.compile(r"(?<![\d.,])(0|12|15|25)\s*$")

#: How many column names a line needs before it counts as a table heading. A
#: lone "Mva" over a total is a label, not a column, and reading it as one would
#: turn every discount percentage in the document into a VAT rate.
_MIN_HEADING_COLUMNS = 4


def _printed_rate(lines: list[str]) -> Decimal | None:
    """A percentage printed on a VAT line, as a fraction.

    The rate is looked for on the labelled line first and on the next line
    second, because a table header written in two rows comes out of a text layer
    as two lines::

        Mva
        (25 %)

    which is one column heading and reads as a VAT label with no rate followed
    by a rate with no label. Only a line that is *nothing but* a percentage is
    accepted there, so the search cannot wander into the item table and pick up
    a discount.
    """
    rates = _printed_rates(lines)
    # More than one rate printed is not a rate. A FINN receipt states 25 % on
    # the postage and 0 % on the fee, over a purchase from a private seller that
    # carries no VAT at all; picking either one and applying it to the whole
    # total proposes a deduction on 3 565 kroner that does not exist. The
    # ambiguity is handed to the user instead — see `apply_type`, which says so.
    if len(rates) != 1:
        return None
    return rates[0]


def _printed_rates(lines: list[str]) -> list[Decimal]:
    """Every distinct VAT rate the document prints, in the order they appear."""
    found: list[Decimal] = []
    for index, line in enumerate(lines):
        low = line.lower()
        if not _has_label(low, _VAT_LABELS):
            continue
        if any(word in low for word in _NOT_VAT):
            continue
        match = _RATE_RE.search(line)
        if match is None:
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if _BARE_RATE.match(following) is None:
                continue
            match = _RATE_RE.search(following)
        if match is None:
            continue
        value = parse_amount(match.group(1))
        if value is None or not 0 <= value <= 100:
            continue
        rate = value / 100
        # Zero is a rate a document can state and a rate we can act on, but it
        # is not one that competes with 25 % for "the rate of this bilag": a
        # line reading "Mva. 0 %" beside one reading "Mva. 25 %" is two rates.
        if rate not in found:
            found.append(rate)
    return found or _column_rates(lines)


def _is_mva_heading(line: str) -> bool:
    """Whether a line is a table heading whose last column is the VAT one."""
    stripped = line.strip()
    # A heading names columns; it does not carry figures. This is what keeps
    # "Org.nr.: NO 991 107 711 MVA" out, which ends in the right word and is a
    # registration number.
    if any(char.isdigit() for char in stripped):
        return False
    if len(stripped.split()) < _MIN_HEADING_COLUMNS:
        return False
    return _MVA_LAST_COLUMN.search(stripped) is not None


def _column_rates(lines: list[str]) -> list[Decimal]:
    """Rates read off the MVA column of an item table.

    A rate does not have to sit on a line that says "mva". On an invoice with a
    proper item table it sits under a column heading, and the row underneath
    carries nothing but the article and its figures::

        NOKARTIKKEL ENH ANTALL PRIS BELØP IMVA
        0411011 Aksjeselskap STK 1,0 6 825,00 6 825,000%

    That is Brønnøysundregistrene's registreringsgebyr, and the "0%" welded to
    the end of the amount is the whole of what the invoice says about VAT. Read
    only for labelled lines it says nothing, and the form fell back to the
    category's default of 25 %, proposing a deduction of 1 365 kroner on a fee
    that carries none.

    Used only when no labelled line stated a rate. A document that says "Sum
    mva 25 %" outright is answering the question directly, and a column is a
    weaker way of being told the same thing.
    """
    headings = [index for index, line in enumerate(lines) if _is_mva_heading(line)]
    if not headings:
        return []
    # Under a heading that carries the "%" itself, a cell is a bare number, and
    # a bare number ends plenty of lines that are not rows: «Postboks 12» read
    # as a second rate of 12 %. So a bare rate counts only below such a heading
    # and only at the end of a row of money figures.
    bare_from = next((index for index in headings if _PERCENT_HEADING.search(lines[index])), None)
    found: list[Decimal] = []
    for index, line in enumerate(lines):
        match = _ROW_RATE.search(line.strip())
        if (
            match is None
            and bare_from is not None
            and index > bare_from
            and len(amounts_in(line)) >= 2
        ):
            match = _ROW_BARE_RATE.search(line.strip())
        # The row has to be a row. A percentage on a line with no amounts on it
        # is prose, a footnote or a heading, not a cell in the MVA column.
        if match is None or not amounts_in(line):
            continue
        value = parse_amount(match.group(1))
        if value is None or not 0 <= value <= 100:
            continue
        rate = value / 100
        if rate not in found:
            found.append(rate)
    return found


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
    assumes, ``"Ordre"`` when the label said so — so that a webshop receipt is
    described as the order it is — and ``"Avregning"`` for a settlement.
    """
    for line in lines:
        low = line.lower()
        for label in _NUMBER_LABELS:
            position = low.find(label)
            if position == -1:
                continue
            match = _INVOICE_NUMBER_RE.match(line, position + len(label))
            if match is not None and len(match.group(1)) <= 32:
                if label in _ORDER_LABELS:
                    return match.group(1), "Ordre"
                if label in _SETTLEMENT_LABELS:
                    return match.group(1), "Avregning"
                return match.group(1), None
    return None, None


def _orgnrs(text: str) -> tuple[str, ...]:
    """Every organisasjonsnummer in the text, deduplicated, order preserved.

    The mod-11 check digit is what makes this usable at all: nine digits also
    turn up in reference and phone numbers, and only about one in eleven of those
    passes.
    """
    found: list[str] = []
    for match in _ORGNR_RE.finditer(text):
        digits = match.group(1) + match.group(3) + match.group(4)
        try:
            valid = validate_orgnr(digits)
        except InvalidOrgnrError:
            continue
        if valid not in found:
            found.append(valid)
    return tuple(found)


#: Words that are part of an app rather than part of a bilag.
#:
#: A phone screenshot puts these exactly where a letterhead sits, at the top of
#: the page, and the last-resort rule that takes the first name-shaped line off
#: the top of a receipt cannot tell the difference. A bank app's "Add receipt"
#: button and an AliExpress search box came back as the motpart of a purchase
#: whose seller was named three screenshots further down.
#:
#: Shared with :mod:`myntos.domain.bilag.extraction`, which applied the same
#: list to the model's answers and not to the parser's. That asymmetry is what
#: let these through: the model is asked for a name only where the parser found
#: none, so a filter on one side alone filters nothing.
NOT_A_PARTY = frozenset(
    {
        # Fields and headings on any receipt.
        "levering",
        "frakt",
        "betaling",
        "amount",
        "details",
        "summary",
        "status",
        "selger",
        "kjøper",
        "seller",
        "buyer",
        "total",
        "totalt",
        "beløp",
        "dato",
        "ordre",
        "order",
        "faktura",
        "kvittering",
        "shipping",
        "delivery",
        "payment",
        # Buttons, tabs and navigation, English and Norwegian.
        "add receipt",
        "upload",
        "show more",
        "show less",
        "view all",
        "edit address",
        "home",
        "payments",
        "key figures",
        "more",
        "account",
        "accounts",
        "search",
        "back",
        "menu",
        "settings",
        "share",
        "done",
        "cancel",
        "confirm",
        "processing",
        "processed",
        "completed",
        "reserved",
        "to pay",
        "vis mer",
        "vis mindre",
        "se alle",
        "hjem",
        "betalinger",
        "konto",
        "kontoer",
        "søk",
        "tilbake",
        "meny",
        "innstillinger",
        "del",
        "ferdig",
        "avbryt",
        "behandles",
        "fullført",
        "last opp",
        # The register a business is in, printed beside its number by law
        # (foretaksregisterloven § 10-2): «Foretaksregisteret: NO968712233MVA».
        "foretaksregisteret",
        "enhetsregisteret",
    }
)

#: How a phone truncates a line it has no room for.
_TRUNCATED = ("...", "…")


def _clean_name(line: str) -> str | None:
    """A line reduced to a company name, or ``None`` if it is not one."""
    # Tested before the strip below takes the ellipsis off. A line a phone cut
    # short is a label with no room, "Order ID, product or sto...", and a
    # company never prints its own name truncated.
    if line.rstrip().endswith(_TRUNCATED):
        return None
    cleaned = _NAME_PREFIX.sub("", line.strip()).strip(" .:,-|")
    low = cleaned.lower()
    if not 2 <= len(cleaned) <= 120:
        return None
    if low in NOT_A_PARTY:
        return None
    if any(word == low or low.startswith(f"{word} ") for word in _TITLE_WORDS + _HEADINGS):
        return None
    # What is left of a field once its value has been taken off it. Returning
    # None here rather than the label is what lets the model's MOTPART fill the
    # gap: an empty counterparty is a question, "Org.nr" is a wrong answer that
    # looks like a read one.
    if _NOT_ALNUM.sub("", low) in _LABEL_WORDS:
        return None
    # Contact details rather than an identity.
    if _NOT_A_NAME.search(cleaned) is not None:
        return None
    if _LETTERSPACED.match(cleaned) is not None:
        return None
    # "[Page 1]", "[Slide 4]" — the headings Løynd's extractor writes above each
    # division of a document. They sit exactly where a sender's name sits, at
    # the top of the text, and they are mostly letters.
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return None
    # A name is a name and not a clause. "en valgte deg. Vi oppdaterer
    # informasjonen her når" is a sentence off a FINN status page that was
    # offered as the counterparty because it was the right length and mostly
    # letters. Six words holds every real one in the fixture set with room:
    # "Linjeforeningen Elektra", "FEARLESS PC Accessories Store".
    if len(cleaned.split()) > _MAX_NAME_WORDS:
        return None
    # A colon left in the middle is a label that was not fully stripped
    # ("Foretaksnavn:DIALGA AS"), and three commas is an address block written
    # on one line. Neither is what a company calls itself.
    if ":" in cleaned or cleaned.count(",") > 2:
        return None
    if _ADDRESS_LINE.search(cleaned) is not None:
        return None
    # Mostly letters, or it is an address line or a total rather than a name.
    letters = len(_LETTER.findall(cleaned))
    return cleaned if letters >= 2 and letters >= len(cleaned) / 2 else None


def _seller_orgnr(orgnrs: tuple[str, ...], own_orgnr: str | None) -> str | None:
    """Which organisasjonsnummer to look for the sender's name beside.

    The first one on the page, **except our own**. That exception is the whole
    reason this function exists: the name is looked for next to the number, and
    on a received invoice that prints the recipient's organisasjonsnummer above
    the sender's — Brønnøysundregistrene's own faktura does exactly this, with
    the customer's number under a heading at the top and its own in a footer —
    anchoring on the first number finds our own reference block and reads a
    field label out of it.

    ``own_orgnr`` is ``None`` when :func:`read` is called without knowing whose
    books this is for, and then the old behaviour stands: there is nothing to
    exclude.
    """
    if own_orgnr is None:
        return orgnrs[0] if orgnrs else None
    other = next((orgnr for orgnr in orgnrs if orgnr != own_orgnr), None)
    # Falling back to our own number when it is the only one is deliberate: on
    # an invoice we issued, the name beside it is ours, and `suggest` needs to
    # see that to work out the direction.
    return other or (orgnrs[0] if orgnrs else None)


def _seller_name(lines: list[str], orgnr: str | None, *, guess: bool = True) -> str | None:
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

    ``guess`` turns that last resort off, and the caller turns it off for a
    phone screenshot. See :func:`read`.
    """
    if orgnr is not None:
        # The label and the "NO" in front of the number are separate pieces:
        # «Foretaksregisteret: NO968712233MVA» has both, and as alternatives
        # only the "NO" was taken off, which left «Foretaksregisteret» as the
        # name of every Coop and Tripletex invoice. The register is matched by
        # its stem, because Fatland writes «Foretagsreg:», and that was the
        # name of a slaughterhouse for one run.
        printed = re.compile(
            r"(?i)(?:(?:org\.?\s*nr\.?|organisasjonsnummer|foreta[kg]s\w*\.?)[:\s]*)?"
            r"(?:NO)?[:\s]*" + r"[ .]?".join(orgnr) + r"\s*(?:MVA)?",
        )
        for index, line in enumerate(lines):
            if printed.search(line) is None:
                continue
            remainder = _clean_name(printed.sub("", line))
            if remainder is not None:
                return remainder
            for above in range(max(0, index - 4), index):
                candidate = _name_on(lines, above)
                if candidate is not None:
                    return candidate
            # A footer with a telephone and a second number between the address
            # and the organisasjonsnummer puts the name further up than four
            # lines: Q-Meieriene's is name, street, postal code, «Tlf»,
            # «Meierinr.», then «Org.nr.». Further up is only believed for a line
            # that ends in a legal form, because further up is also where the
            # body of the page ends, and a sentence there is not a company.
            for above in range(max(0, index - 8), max(0, index - 4)):
                candidate = _name_on(lines, above)
                if candidate is not None and _has_legal_form(candidate):
                    return candidate
            break

    labelled = _labelled_name(lines, _SELLER_LABELS)
    if labelled is not None:
        return labelled

    if not guess:
        return None

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


def _has_legal_form(name: str) -> bool:
    words = [word for word in re.split(r"[^0-9a-zæøå]+", name.lower()) if word]
    return bool(words) and words[-1] in _LEGAL_FORMS


#: One word in capitals: how a logo sets the first half of a name.
_LOGO_WORD = re.compile(r"[A-ZÆØÅ][A-ZÆØÅ-]{2,}")


def _name_on(lines: list[str], index: int) -> str | None:
    """The name on ``lines[index]``, with the half a logo put on its own line.

    Fatland's letterhead sets «FATLAND» large and «Jæren AS» under it, so the
    text layer has the company in two lines, and only the second ends in a
    legal form. Read a line at a time, the slaughterhouse was «Jæren AS». A
    word in capitals directly over a name with a legal form, or a name with a
    legal form directly under one, is put back together. Only a single word in
    capitals: a heading or a contact line over a name is not part of it, and
    «FAKTURA» never gets this far, because it is not a name to begin with.
    """
    candidate = _clean_name(lines[index])
    if candidate is None:
        return None
    if _LOGO_WORD.fullmatch(candidate) is not None and index + 1 < len(lines):
        rest = _clean_name(lines[index + 1])
        if rest is not None and _has_legal_form(rest):
            return _logo_name(candidate, rest) or candidate
    if _has_legal_form(candidate) and index > 0:
        logo = _clean_name(lines[index - 1])
        if logo is not None and _LOGO_WORD.fullmatch(logo) is not None:
            return _logo_name(logo, candidate) or candidate
    return candidate


def _logo_name(logo: str, rest: str) -> str | None:
    """«FATLAND» and «Jæren AS» as one name, if the first is not a label."""
    if logo.lower() in _BUYER_LABELS + _SELLER_LABELS or _has_legal_form(logo):
        return None
    joined = f"{logo} {rest}"
    return joined if len(joined.split()) <= _MAX_NAME_WORDS else None


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
    is rarely the first cell out. :data:`_ITEM_SEARCH_LINES` is how far down that
    search goes, and it is generous for the same reason: a two-line column header
    over six columns puts seven unusable lines between the heading and the first
    item, which is exactly what the Elektra invoice does, and a window that
    stopped short of it read the seller's name into the ledger instead of
    "Batteri".
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
        window = lines[index + 1 : index + 1 + _ITEM_SEARCH_LINES]
        # A row of an item table has a price on it. Preferring one that does is
        # what tells a table row from the prose and the labels a wide window
        # also reaches: "Batteri 1 1 152,00 1 152,00 288,00 1 440,00" is a row,
        # while "Fakturanummer" and "Only tested" are a field label and a line
        # of an advert, and all three clean up into something that looks like
        # the name of a thing.
        for candidate in window:
            if amounts_in(candidate) and (item := _clean_item(candidate)) is not None:
                return item
        # Nothing priced. Fall back to the first readable line, because a
        # receipt that lists what was bought above its totals is a real shape
        # and this is the only thing that reads it.
        for candidate in window:
            if (item := _clean_item(candidate)) is not None:
                return item
        return None
    return None


def _clean_item(line: str) -> str | None:
    """One line of an item table, if it reads as the name of a thing."""
    # A price at the end of the row is the row's price, not part of its name.
    # Trimmed before anything else so that what follows judges the words.
    cleaned = " ".join(line.split())
    for _ in range(_MAX_FIGURE_COLUMNS):
        shorter = _TRAILING_FIGURE.sub("", cleaned).strip(" .:,-|*")
        if shorter == cleaned:
            break
        cleaned = shorter
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
    # A field label is not a thing that was bought. The same list the
    # counterparty reader uses, for the same reason and against the same
    # mistake: "Fakturanummer" reached the ledger as the description of a
    # 6 825-krone registration fee, and "Selger" as the description of an SSD.
    if _NOT_ALNUM.sub("", cleaned).casefold() in _LABEL_WORDS:
        return None
    # And one that names a field this list has not met. A single word ending in
    # -dato, -nummer, -nr or -kode is a header field whose value has just been
    # stripped off the end of it — "Ordredato 01.09.2026" becomes "Ordredato" —
    # and no invoice sells one. Only single words, because a real item name may
    # perfectly well end in a part number.
    if len(cleaned.split()) == 1 and _LABEL_SUFFIX.search(cleaned) is not None:
        return None
    # The same in two words. An AliExpress VOEC invoice prints "Order Number:
    # 3076911632201854" as the first line under its item heading, and with the
    # number stripped "Order Number" was the item every one of them sold.
    if cleaned.casefold() in _NUMBER_LABELS:
        return None
    # "F A K T U R A" passes every test above — it is the right length, it is
    # all letters, and it has no figures in it. It is also the word FAKTURA set
    # with letter spacing, and it reached the ledger as the description of a
    # 6 825-krone registration fee.
    if _LETTERSPACED.match(cleaned) is not None:
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


def read(
    text: str,
    *,
    today: date,
    title: str | None = None,
    own_orgnr: str | None = None,
    has_letterhead: bool = True,
) -> InvoiceReading:
    """Extract what ``text`` states, without deciding anything about it.

    ``title`` is what the file says about itself, from the PDF's own metadata.
    It is kept out of ``text`` on purpose rather than pasted on top of it: every
    amount, date and organisasjonsnummer in here is found by looking at lines,
    and a line that did not come off the page has no business being one of them.
    It is used for exactly two things, both of which are named where they
    happen: the seller's name when the body does not give one, and the sentence
    the model is shown alongside the excerpt.

    ``own_orgnr`` is the one exception to "without deciding anything": the
    sender's name is looked for beside an organisasjonsnummer, and which number
    that is depends on which of them is not the reader's own. See
    :func:`_seller_orgnr`. Everything else here is read the same way whoever is
    asking, and passing nothing reproduces the reading exactly.

    ``has_letterhead`` is false when the top of this text is a phone screenshot.
    It turns off the two rules that read a sender out of a *position* rather
    than out of a statement: the first name-shaped line near the top, and the
    file's own title. Both are sound on a document, where the top of page one is
    where a company prints its name, and neither survives contact with an app,
    where the same position holds a status bar, a search box and a row of tabs.
    "Add receipt", "Order ID, product or sto...", "IMG 9362" and "Returns/re"
    all reached the motpart field of one AliExpress purchase that way, and each
    fix produced the next one, because a list of words that are not names cannot
    keep up with an interface.

    Nothing else changes. A name printed beside an organisasjonsnummer, or after
    "Selger" or "Sold by", is a statement and is still read. What is left is a
    blank field, which is the honest answer and the one the user can fill in.
    """
    document_title = _clean_title(title)
    named_by_title = document_title if has_letterhead else None
    if not text.strip():
        return InvoiceReading(
            document_title=document_title,
            seller_name=named_by_title,
            seller_named_by_title=named_by_title is not None,
        )

    lines = _rejoin_wrapped_amounts([line.strip() for line in text.splitlines()])
    gross, gross_is_negative = _total(lines)
    net = _labelled_amount(lines, _NET_LABELS)
    vat = _labelled_amount(lines, _VAT_LABELS, exclude=_NOT_VAT, allow_zero=True)

    # Any two of the three give the third, and invoices state at least two.
    if gross is None and net is not None and vat is not None:
        gross = net + vat
    elif net is None and gross is not None and vat is not None:
        net = gross - vat

    printed_rates = tuple(_printed_rates(lines))
    rate = _printed_rate(lines)
    if rate is None and vat is not None and net is not None and net > 0:
        rate = vat / net
    # The fourth quantity closes the same triangle. An invoice whose item table
    # prints the gross in an unlabelled last column and states the rate only as
    # a column heading — "Mva / (25 %)" — gives both of these and no VAT line at
    # all, and the amount is then arithmetic rather than a guess.
    if vat is None and gross is not None and rate is not None and rate > 0:
        vat = (gross - gross / (1 + rate)).quantize(Decimal("0.01"))
        if net is None:
            net = gross - vat
    # A printed 0 % closes the same triangle: the VAT is nothing and the net is
    # the whole amount. Handled here rather than by `_snap_rate`, which knows
    # only the rates that have a deduction behind them and drops zero.
    elif vat is None and gross is not None and rate == 0:
        vat = Decimal(0)
        if net is None:
            net = gross

    invoice_date, due_date, date_is_labelled = _dates(lines, today=today)
    number, number_kind = _invoice_number(lines)
    item_text = _item_text(lines)
    # Several files naming different things are several orders, not one, and
    # the first of each is no more the bilag than any other. One card charge for
    # nine AliExpress orders was described as "Faktura NO0110…095, 5840-31ZY
    # Worm gear reducer mot": the number of one invoice and the item of an
    # unrelated order, neither of them what the 9 771,76 kroner paid for. A
    # blank here leaves the description to the model, which is shown every
    # document, and the number to the user. A bank screenshot names no item
    # and no number, so the ordinary bilag (one invoice, its bank line) is not
    # touched.
    groups = _in_each_document(lines)
    if len(groups) > 1:
        numbers = {found for group in groups if (found := _invoice_number(group)[0])}
        if len(numbers) > 1:
            number, number_kind = None, None
        items = {found for group in groups if (found := _item_text(group))}
        if len(items) > 1:
            item_text = None
    orgnrs = _orgnrs(text)
    low = text.lower()
    printed_seller = _seller_name(lines, _seller_orgnr(orgnrs, own_orgnr), guess=has_letterhead)
    seller_name = _a_name(printed_seller or named_by_title)

    return InvoiceReading(
        invoice_date=invoice_date,
        date_is_labelled=date_is_labelled,
        due_date=due_date,
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        vat_rate=Decimal(0) if vat == 0 else _snap_rate(rate),
        printed_rates=printed_rates,
        invoice_number=number,
        number_kind=number_kind,
        orgnrs=orgnrs,
        seller_name=seller_name,
        seller_named_by_title=printed_seller is None and seller_name is not None,
        buyer_name=_buyer_name(lines),
        currency_code=_currency(text),
        is_credit_note="kreditnota" in low or "credit note" in low,
        under_voec=_VOEC.search(text) is not None,
        total_is_negative=gross_is_negative,
        shows_a_return=_shows_a_return(_document_totals(lines)),
        item_text=item_text,
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
_TITLE_EXTENSION = re.compile(r"(?i)\.(pdf|docx?|html?|txt|png|jpe?g|webp|gif|heic)$")

#: What a camera roll calls a photograph, and what a phone calls a screenshot.
#: Never a company, however name-shaped the rest of the title is.
_CAMERA_ROLL = re.compile(
    r"(?i)^(?:img|pxl|dsc|dcim|photo|foto|bilde|image|screenshot|skjermbilde|skjermdump)"
    r"[\s_.-]*\d"
)


def is_camera_roll(name: str) -> bool:
    """Whether ``name`` is what a phone called a photo, and so says nothing."""
    return _CAMERA_ROLL.match(name.strip()) is not None


_TITLE_KIND = re.compile(
    r"(?i)^(?:order\s+details|order\s+confirmation|order|receipt|invoice|"
    r"ordrebekreftelse|kjøpsbekreftelse|kvittering|faktura|bilag|dokument|document|"
    # Not \b: an underscore is a word character, so \b would refuse to end the
    # word in "faktura_104588" and leave an invoice number standing in for a
    # company name.
    r"scan|scanned|download)(?![a-zæøå0-9])[\s:_.-]*"
)


def _clean_title(title: str | None) -> str | None:
    """The part of a document title worth treating as a name.

    The title earns its place because of what it often is: an eBay order
    confirmation says "eBay" in its title and nowhere in its text. A photograph
    has a title too, and it is the camera roll's: "IMG_9362". That one reached
    the motpart field of an AliExpress purchase, because `read` falls back to
    the title when no sender is printed and four phone screenshots print none.

    So a title has to look like a name to be used as one. Mostly letters is the
    same test :func:`_clean_name` applies to a line, and it is what separates
    "eBay" and "Faktura 1075, Telenor" from "IMG 9362" and "PXL 20260905".
    """
    if title is None:
        return None
    collapsed = _TITLE_EXTENSION.sub("", " ".join(title.split()))
    parts = [part.strip() for part in _TITLE_TAIL.split(collapsed) if part.strip()]
    # Last part first: a title reads "what this is | who it is from", and it is
    # the second half that names anybody.
    for part in reversed(parts):
        candidate = _TITLE_KIND.sub("", part).strip(" .:,-|_")
        if not 2 <= len(candidate) <= 80:
            continue
        if _CAMERA_ROLL.match(candidate) is not None:
            continue
        letters = len(_LETTER.findall(candidate))
        if letters >= 2 and letters >= len(candidate) / 2:
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
    # The document says the buyer wrote it (issued_by_buyer): a settlement, the
    # farm's own sales document. Read by the questions, which otherwise ask
    # whether money in from an unregistered sender is a sale at all.
    issued_by_buyer: bool = False
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


#: Words that put a verb or a person in a line, and so put a sentence there.
#:
#: Deliberately short and deliberately not "og" or "the": "Regnskap og Revisjon
#: AS" and "The North Face" are companies, and a list that grows until it starts
#: refusing real names has stopped being a safety net.
_CLAUSE_WORDS = frozenset(
    {
        "har",
        "kan",
        "skal",
        "vil",
        "må",
        "er",
        "ble",
        "blir",
        "var",
        "ikke",
        "hvis",
        "når",
        "som",
        "mot",
        "deg",
        "din",
        "ditt",
        "dine",
        "meg",
        "min",
        "mitt",
        "vår",
        "vårt",
        "click",
        "tap",
        "press",
        "check",
        "view",
        "show",
        "add",
        "edit",
        "your",
        "you",
    }
)


def _a_name(candidate: str | None) -> str | None:
    """``candidate`` if it is a name, and nothing if it is a sentence.

    The last gate on what the parser found, applied to the name itself rather
    than to what is done with it. A clause that reaches ``seller_name`` does not
    only fill the motpart field: it becomes the bilagstekst when the item line
    is missing, and it is what a registered party is matched against. "Forsikre
    produktet mot uhell" is a checkbox on FINN sitting one line under "Selger",
    which is a label and normally tells the truth.
    """
    return candidate if offerable(candidate) else None


def offerable(name: str | None) -> bool:
    """Whether a name is worth offering as a motpart the user can register.

    A stricter question than whether it is worth *showing*. A name that only
    goes into the bilagstekst is read once and forgiven; one offered here can
    become a row in the party register that outlives the voucher, and a register
    full of clauses off a FINN status page is worse than one that is short.

    So the two rules that catch a clause are applied again here, over the name
    the parser found as well as the model's: a cap on the word count, and the
    observation that lowercase plus more than one word is a sentence fragment
    ("en har sendt pakken"). A lone lowercase token is left alone, because
    marketplace handles are most of the counterparties this system meets.
    """
    if name is None:
        return False
    words = name.split()
    if not words or len(words) > _MAX_NAME_WORDS:
        return False
    if name[:1].islower() and len(words) > 1:
        return False
    # A colon in the middle is a label with its value stuck to it, the same rule
    # `_clean_name` applies to a line. "Ebay O:10-15068-4" is eBay plus an order
    # reference, and a supplier registered under that name is one nothing will
    # ever match against again.
    if ":" in name:
        return False
    # A capital letter at the front is not proof of a name. "Forsikre produktet
    # mot uhell" is a checkbox on FINN, and it passes every rule above. The
    # words below are what a clause is made of and what a company name is not:
    # verbs, pronouns and the prepositions that join them.
    return not any(_NOT_ALNUM.sub("", word).casefold() in _CLAUSE_WORDS for word in words)


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


#: Words with which a document says the *buyer* wrote it on the seller's behalf:
#: self-billing, which bokføringsforskriften § 5-2-1 allows and EHF carries as
#: Selvfakturering from 2027. On such a document the name at the top is the
#: customer, and every rule below that reads "issued by somebody else" as a
#: purchase would read it backwards.
_SELF_BILLED = (
    "selvfaktura",
    "selvfakturering",
    "self-billing",
    "self billing",
    "utstedt av kjøper",
    "utstedt av kjøperen",
)

#: What a farm's buyers call the settlement they write: Tine's melkeoppgjør,
#: Nortura's slakteoppgjør, the mill's kornoppgjør, the timber buyer's
#: tømmeroppgjør. The same paper as self-billing, under the names the trade
#: uses (bokføringsforskriften § 5-2-1 tredje ledd lets cooperatives and
#: approved buyers issue it). Read only in books with the farming add-on,
#: because "oppgjør" alone is also the word on a power company's bill.
_FARM_SETTLEMENTS = (
    "melkeoppgjør",
    "melkeavregning",
    "slakteoppgjør",
    "slakteavregning",
    "kornoppgjør",
    "kornavregning",
    "tømmeroppgjør",
    "tømmeravregning",
    "produsentoppgjør",
    "leverandøroppgjør",
    "leveranseoppgjør",
    "avregning levert melk",
    "avregning for levert",
    # Q-Meieriene's heading. Without it the September melkeoppgjør was read
    # as a sale only because the farm's own name was at the top, with no word
    # that it was a settlement and no prompt to split it into lines.
    "avregningsoppgave",
    "oppgjør for levert",
)

#: How the writer of an «avregning» says it is paying the reader. Fatland heads
#: its slaughter settlement «Avregning nr.» and names no product or trade word
#: anywhere: what makes it a settlement and not a power company's avregning is
#: that it is paid out («Utbetalingsdato», «Utbetalt:»), where a bill says what
#: is owed and when it falls due.
_PAID_OUT = ("utbetalingsdato", "utbetalt", "utbetales", "til utbetaling", "godskrives")


def issued_by_buyer(text: str, *, farm: bool = False) -> bool:
    """Whether the document says it was written by the buyer, for the seller.

    ``farm`` adds the settlement names a farm's buyers use, which mean that in a
    farm's books and could mean something else in anyone else's, and an
    «avregning» that pays out: a farm's buyer may call its settlement no more
    than that.
    """
    lowered = " ".join(text.lower().split())
    words = _SELF_BILLED + (_FARM_SETTLEMENTS if farm else ())
    if any(word in lowered for word in words):
        return True
    return farm and "avregning" in lowered and any(word in lowered for word in _PAID_OUT)


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

    if reading.under_voec and suggestion.direction is CashFlowDirection.UT:
        # VAT a foreign web shop charged under VOEC is a consumer's VAT. The
        # scheme covers sales to consumers only (Skatteetaten: "VOEC-ordningen
        # er kun for salg til forbrukere (B2C)"); a business buying there should
        # give its organisasjonsnummer and have the import VAT charged at the
        # border instead, which is the VAT that can be deducted. What a VOEC
        # seller charged can only be refunded by that seller, never deducted.
        #
        # Printed in kroner at 25 %, it looked exactly like Norwegian input VAT,
        # and the parser proposed nothing either way. AliExpress's bilag of nine
        # orders on one card charge (9 771,76) came back with no code, so the
        # form fell back to the category: 0 for an unregistered company, without
        # a word about the 25 % on the invoices, which read as a mistake; and
        # 25 % for a registered one, deducting 1 954 kroner of VAT when 333 was
        # charged and none of it was deductible. The 5 908,81 spot welder in
        # that charge was over the VOEC limit and carried no VAT at all.
        #
        # Before the currency, because a VOEC invoice prints its own currency
        # beside the kroner, and "ingen norsk mva" would be untrue of it.
        suggestion.tax_code = NO_VAT_CODE
        suggestion.mark("tax_code")
        # "Betalt" first, because code 0 was read as "no VAT was paid", which
        # the invoices contradict. And the advice is for any business, not only
        # a registered one: VOEC leaves out every næringsdrivende, and the import
        # VAT an unregistered company pays instead is what it can claim in
        # tilbakegående avgiftsoppgjør (§ 8-6) once it registers.
        warn(
            "Mvaen på bilaget er betalt etter VOEC-ordningen, som bare gjelder salg til "
            "forbrukere. Den mvaen gir ikke fradrag, så mva-koden er satt til 0 og hele beløpet "
            "føres som kostnad. Oppgi organisasjonsnummeret når selskapet handler, også før det "
            "er mva-registrert: da beregnes mvaen i stedet ved innførselen, og den gir fradrag "
            "når selskapet er registrert. Varer over 3 000 kr er utenfor ordningen, og mvaen på "
            "dem kan komme som en egen regning fra transportøren."
        )
        return

    if reading.currency_code is not None:
        # Whatever tax the seller charged in GBP, it is not norsk inngående mva
        # and there is no code here that would make it deductible. The purchase
        # may owe Norwegian VAT the other way round, which is the user's to
        # settle and not something a form may assume on their behalf.
        #
        # But "no code" was not neutral. The form fills an empty code with the
        # category's default, which is 25 % on nearly every purchase, so MA-3D's
        # jet thrusters (1 790 EUR, "VAT 0,00 €") came back deducting 3 878
        # kroner of VAT nobody had charged. The import VAT on them arrives later
        # on the freight forwarder's bill and is its own entry. So a purchase is
        # proposed at 0: true about this invoice, and the warning says where the
        # rest goes. A category whose default is the import VAT itself keeps it.
        default = entry.default_tax_code if entry is not None else None
        if suggestion.direction is CashFlowDirection.UT and default not in _IMPORT_VAT_CODES:
            suggestion.tax_code = NO_VAT_CODE
            suggestion.mark("tax_code")
            warn(
                f"Bilaget er i {reading.currency_code}, så det er ingen norsk mva på det, og "
                "mva-koden er satt til 0. Er det varer, kommer innførselsmvaen på en egen "
                "regning fra speditøren eller Tolletaten: før den for seg som «Mva betalt ved "
                "innførsel (tollpass)». Er det en tjeneste, kan kjøpet utløse omvendt "
                "avgiftsplikt."
            )
            return
        warn(
            f"Bilaget er i {reading.currency_code}, så avgiften på det er ikke norsk inngående "
            "mva. Velg mva-kode selv. Kjøp fra utlandet kan utløse innførselsmva eller omvendt "
            "avgiftsplikt."
        )
        return

    rate = reading.vat_rate
    default = entry.default_tax_code if entry is not None else None
    if rate is None and len(reading.printed_rates) > 1:
        shown = ", ".join(f"{int(each * 100)} %" for each in reading.printed_rates)
        warn(
            f"Bilaget viser flere mva-satser ({shown}), så vi foreslår ingen mva-kode. "
            "Gjelder satsene ulike deler av kjøpet, hører de til hver sin føring."
        )
        return
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
    elif (
        reading.shows_a_return
        and entry is not None
        and entry.direction is CashFlowDirection.INN
        and default not in (None, NO_VAT_CODE)
    ):
        # The bilag shows the money leaving and the money coming back, so what
        # came in is a return and not a sale. Filed under a sales category it
        # adds turnover the company never had and output VAT it does not owe:
        # 25 % of the 26 266,40 eBay sent back is 5 253 kroner of tax on a
        # purchase that was cancelled.
        #
        # Said rather than overruled. The catalogue now has somewhere to put
        # it — the refusjon categories credit the account the purchase debited
        # and send the VAT back to 2710 — but which purchase it reverses is not
        # something the document says, and picking one for the user would put a
        # cost on an account they never chose.
        warn(
            f"Bilaget viser både en betaling ut og en inn på samme motpart, så dette ser ut "
            f"som en refusjon og ikke et salg. «{entry.label}» legger utgående mva på beløpet. "
            "Velg refusjonen som svarer til det kjøpet pengene kom tilbake på, under «Penger "
            "tilbake på et kjøp»."
        )
        return
    elif reading.vat_amount == 0:
        # The document says there is no VAT on this, and saying so used to end
        # in a warning and nothing else. With no code proposed the form fell
        # back to the category's default, which is 25 % on most of them:
        # Brønnøysundregistrene's registreringsgebyr, printed "0 %" in its own
        # column, came back as a 1 365-krone deduction that does not exist.
        # What the bilag says beats what the category assumes, here as in the
        # branch above.
        #
        # Purchases only. A sale without VAT is a different question with
        # several answers — export, outside the VAT area, exempt with credit —
        # and the code decides what the mva-melding reports, so the user picks.
        if suggestion.direction is CashFlowDirection.UT:
            suggestion.tax_code = NO_VAT_CODE
            suggestion.mark("tax_code")
            if default is not None and default != NO_VAT_CODE:
                # The reverse-charge caution is only added where it could apply.
                # A document printing a Norwegian organisasjonsnummer is a
                # Norwegian document, and telling the user that a gebyr from
                # Brønnøysundregistrene might be an import is noise on top of a
                # sentence that is already telling them something.
                abroad = (
                    " Kjøp fra utlandet kan utløse omvendt avgiftsplikt."
                    if not reading.orgnrs
                    else ""
                )
                warn(
                    "Fakturaen er uten mva, så mva-koden er satt etter fakturaen og ikke "
                    f"etter typen.{abroad}"
                )
                return
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
    direction: CashFlowDirection | None = None,
    farm: bool = False,
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

    ``direction`` is the user's, when they clicked one. It beats everything the
    document suggests, the sign on a bank line included, because the person who
    made the payment knows which way it went and every rule here is inference.
    It is not marked as a proposal: the user chose it, and the form keeps it.

    ``farm`` is whether the books have the farming add-on, which makes a
    melkeoppgjør or a slakteoppgjør read as what it is: a sale the buyer wrote
    up (:func:`issued_by_buyer`).
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
    seller_is_us = found.seller_name is not None and _normalise_name(
        found.seller_name
    ) == _normalise_name(own_name)
    # Being named first is evidence only when there is nobody else to be named.
    #
    # It used to be enough on its own, and that was wrong in the expensive
    # direction. Brønnøysundregistrene's own faktura prints the *customer's*
    # organisasjonsnummer under a heading at the top and its own in a footer, so
    # a company being invoiced for its own registration was named first on a bill
    # it had received — and a 6 825-krone fee came back as "Salg med 25 % mva",
    # because the category menu is narrowed by the direction and a sale was the
    # only kind of answer on offer.
    #
    # With two parties printed, the name beside the *other* number decides it:
    # `seller_is_us` is the reading that actually means we sent this. With one,
    # ours, there is no other candidate and first still means sender.
    only_us = found.orgnrs == (own_orgnr,)
    we_issued_it = seller_is_us or only_us
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

    # A settlement the buyer wrote for us. It outranks the register's word on
    # the party, because Felleskjøpet is a supplier of feed and the buyer of the
    # grain on the same orgnr, and the document says which of the two this is.
    # The name at the top is the customer's, so the counterparty is that name.
    #
    # Without this a melkeoppgjør from Tine came back as a purchase from Tine,
    # and since the category menu is narrowed by direction, the model was never
    # even offered «Melkeoppgjør».
    by_buyer = issued_by_buyer(text, farm=farm)
    suggestion.issued_by_buyer = by_buyer
    if by_buyer:
        suggestion.direction = CashFlowDirection.INN
        suggestion.mark("direction")
        suggestion.warnings.append(
            "Dette ser ut som et oppgjør kjøperen har skrevet ut for deg. Det er "
            "salgsdokumentet ditt, ikke en regning. Har oppgjøret trekk (avgifter, andel, "
            "fôr), før det som et sammensatt bilag med én linje per post."
        )

    # Last word to the sign, because it is the only statement about direction
    # the document makes. Everything above it is inference: who is named first,
    # whose organisasjonsnummer this is, whether the register knows them. A bank
    # line that reads "NOK 26 266,40" with no minus is money that arrived, and
    # it says so in the one place the page has to be right.
    #
    # This is what an eBay refund looks like. Three screenshots, two eBay
    # transactions, one out and one back, and every rule above it reads
    # "purchase" from a purchase's paperwork. The user is registering the money
    # coming back.
    if found.total_is_negative is not None:
        suggestion.direction = (
            CashFlowDirection.UT if found.total_is_negative else CashFlowDirection.INN
        )
        suggestion.mark("direction", source_of("gross_amount"))

    # The user's word, after every inference and before the category, which is
    # guessed from the direction: a sale by its rate, a purchase by its words.
    if direction is not None:
        suggestion.direction = direction
        suggestion.sources.pop("direction", None)

    if suggestion.counterparty_name is None:
        suggestion.counterparty_name = other_name
    suggestion.counterparty_orgnr = counterparty_orgnr
    # Named on the bilag but not in the register. The field is still filled in:
    # the form offers the name as its own choice, and registers it under that
    # name if the user posts with it selected. So the proposal is marked like
    # any other, with the origin of the name rather than of a party row.
    if party is None and offerable(suggestion.counterparty_name):
        suggestion.mark("party_id", source_of("buyer_name" if we_issued_it else "seller_name"))

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
        # Not on a settlement: what the buyer pays out is the sale less what it
        # deducted, so it is meant not to be the net plus the VAT, and «Kontroller
        # totalen» sent the farmer checking a total that was right.
        if (
            not by_buyer
            and found.net_amount is not None
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
    # The amount is a difference, and a difference does not look like one in a
    # field. The eBay bilag shows 27 850,81 out and 26 266,40 back for the same
    # GBP 2 137,00, and the 1 584,41 left over is the exchange rate moving
    # between the two, not something that was bought. Left unexplained it reads
    # as a small purchase.
    if found.shows_a_return and found.gross_amount is not None:
        # Both halves of the advice now name a category that exists. Until the
        # catalogue had 8160 and 8060 this could only say what the amount was
        # and leave the user to file it as "Annen driftskostnad", which puts a
        # currency loss among the driftskostnader the næringsspesifikasjon adds
        # up as running expenses.
        went_out = bool(found.total_is_negative)
        way = "ut" if went_out else "inn"
        kind = "et valutatap" if went_out else "en valutagevinst"
        label = "Valutatap" if went_out else "Valutagevinst"
        suggestion.warnings.append(
            f"Bilaget viser både en betaling ut og en inn på samme motpart, så beløpet her er "
            f"differansen mellom dem: {_written(found.gross_amount)} kr {way}. Er det samme "
            f"beløp i utenlandsk valuta begge veier, er differansen {kind} og ikke et kjøp: "
            f"velg «{label}». Hører de to til hver sin føring, del dem opp."
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
