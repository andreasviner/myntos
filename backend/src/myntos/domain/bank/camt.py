"""Reading an ISO 20022 camt.053 bank statement.

camt.053 ("BankToCustomerStatement") is the end-of-day statement every
Norwegian bank exports from its nettbank: DNB, SpareBank 1, Nordea and
Handelsbanken all offer it, mostly as ``camt.053.001.02`` and increasingly as
``.001.08``. It is the format this module reads, and the only one: camt.052 is
an intraday report whose entries may still change, and camt.054 is a
notification of single movements with no balances to check them against. Both
are refused by name rather than read as though they were a statement.

Pure functions over bytes. Nothing here touches the database, so everything the
import refuses is refused before a row exists, and the tests can feed it
hand-built files without standing anything up.

Three decisions, each the answer to a way a statement import goes wrong:

**The XML is parsed with no DTD at all.** The file comes from a user's
download folder, not from the bank over a channel we authenticate, so it is
untrusted input to an XML parser, which is the classic place for an entity
expansion bomb or an external entity that reads a local file. A bank statement
never carries a DTD, so the parser refuses the document the moment it meets a
``<!DOCTYPE``, before any entity can be declared, let alone expanded. That is
stricter than ``defusedxml`` (which tolerates a DTD and refuses its entities)
and needs no dependency: the stdlib's expat is driven directly with every
declaration handler set to refuse.

**The statement has to add up, or nothing is imported.** Opening balance plus
the booked credits minus the booked debits must equal the closing balance, to
the øre, and the bank's own totals (``TxsSummry``) must agree with the entries
when they are present. A file that does not add up has been truncated, edited,
or exported with a filter on, and reconciling the register against it would
mark entries as matched against an account that never looked like that.

**Kroner only, for now.** An entry or an account in another currency is
refused with the entry named. The register can post a foreign-currency purchase
from an invoice, but a bank line carries the charged amount in the account's
currency and nothing else, and guessing a conversion here would put a figure in
the ledger that no document states.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from xml.parsers import expat

from myntos.domain.bank.errors import (
    InvalidBankStatementError,
    UnbalancedBankStatementError,
    UnsupportedStatementCurrencyError,
)
from myntos.domain.ledger.bankkonto import format_bank_account, validate_bank_account
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.errors import InvalidBankAccountError

NOK = "NOK"
ZERO = Decimal("0.00")

# The message family is "camt.053.001.NN". Versions 02 and 08 are the ones
# Norwegian banks export and the ones the tests carry fixtures for. The fields
# read here exist in every version between and after; the one structural
# change that touches them (a related party wrapped in ``Pty`` from the 2019
# versions on) is read in both shapes, so the versions in between are accepted
# rather than refused for a difference that does not reach this code.
_CAMT_NAMESPACE = re.compile(r"^urn:iso:std:iso:20022:tech:xsd:(camt\.\d{3})\.001\.(\d{2})$")
_STATEMENT_MESSAGE = "camt.053"
# What the other camt messages are, so a refusal can say what the file *is*
# rather than only what it is not. A user who exported the wrong report from
# the nettbank needs to know which button to press instead.
_OTHER_MESSAGES = {
    "camt.052": "camt.052 er en kontorapport for dagen, der posteringene ennå kan endre seg",
    "camt.054": "camt.054 er en avisering av enkeltbetalinger, uten saldo å kontrollere mot",
}

# Balance type codes. OPBD is "opening booked"; PRCD, "previously closed
# booked", is what several banks send instead of it, and it is the same figure
# seen from the day before. CLBD is "closing booked". The available-balance
# codes (OPAV, CLAV, ITAV …) include reservations and are not booked figures,
# so they are never what the arithmetic is checked against.
_OPENING_CODES = ("OPBD", "PRCD")
_CLOSING_CODE = "CLBD"
_BOOKED = "BOOK"


@dataclass(frozen=True)
class ParsedEntry:
    """One booked movement on the account (``Ntry``).

    ``amount`` is always positive and ``direction`` says which way the money
    went, the same shape the register uses: the ledger keeps debit and credit
    apart rather than signing amounts, and a bank line that arrived signed
    would be the one place in the system where a minus decided which side of
    the books something landed on.
    """

    booking_date: date
    value_date: date | None
    amount: Decimal
    direction: CashFlowDirection
    currency: str
    counterparty_name: str | None
    # Ustrd, every one of them, joined. What the payer typed in the message
    # field, or what the bank wrote for a card purchase.
    remittance: str | None
    # Strd/CdtrRefInf/Ref. In Norway that is the KID, and it is what ties an
    # incoming payment to the invoice it settles.
    kid: str | None
    end_to_end_id: str | None
    # AcctSvcrRef: the bank's own reference for the booking. Unique per account
    # at the bank that issued it, which is what makes it the dedup key.
    bank_reference: str | None
    # AddtlNtryInf: the bank's free-text line ("Varekjøp", "Overføring"), shown
    # when neither the counterparty nor the message says anything.
    additional_info: str | None
    reversal: bool = False


@dataclass(frozen=True)
class ParsedStatement:
    """One ``Stmt``: one account, one period, its balances and its entries."""

    statement_id: str
    # Eleven digits, the form CompanyBankAccount stores, whatever the file used
    # (a Norwegian IBAN or the bare BBAN in Othr/Id).
    account_number: str
    from_date: date
    to_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    entries: tuple[ParsedEntry, ...]
    # Entries the file carries that are not booked (PDNG, INFO). Not imported:
    # a pending entry can still change or vanish, and the balances the file is
    # checked against are booked balances. Counted so the import can say so.
    skipped_unbooked: int = 0


@dataclass(frozen=True)
class ParsedFile:
    """A whole camt.053 document, which may hold several statements."""

    message_id: str | None
    version: str
    statements: tuple[ParsedStatement, ...] = field(default_factory=tuple)


# --- XML, with every declaration refused ---------------------------------------------


class _DeclarationRefusedError(Exception):
    """A DTD or an entity declaration, met inside the parser."""


def _refuse(*_: object) -> None:
    raise _DeclarationRefusedError


def _refuse_external(*_: object) -> int:
    # Typed to expat's signature for this one handler, which returns a status.
    # It never gets to: the DOCTYPE that could declare an external entity has
    # already been refused, and this is the belt to that braces.
    raise _DeclarationRefusedError


def _parse_xml(content: bytes) -> ET.Element:
    """Parse ``content`` into an element tree, refusing any DTD.

    Expat driven by hand, feeding the stdlib's own TreeBuilder, rather than
    ``ET.fromstring``: the C accelerator behind ``fromstring`` does not expose
    the expat handlers, and the handlers are the whole defence. The DOCTYPE
    handler fires before any declaration inside it is read, so an entity is
    never so much as defined, and parameter entities are switched off for good
    measure.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.NotationDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(content, True)
    except _DeclarationRefusedError as error:
        raise InvalidBankStatementError(
            "filen inneholder en DTD-deklarasjon. Det gjør aldri en kontoutskrift fra banken, "
            "så den blir ikke lest"
        ) from error
    except expat.ExpatError as error:
        raise InvalidBankStatementError(
            "filen er ikke gyldig XML. Last ned kontoutskriften på nytt fra nettbanken"
        ) from error
    return builder.close()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[0] if "}" in tag else ""


def _children(element: ET.Element, name: str) -> Iterator[ET.Element]:
    return (child for child in element if _local(child.tag) == name)


def _child(element: ET.Element | None, *path: str) -> ET.Element | None:
    """The first element down ``path``, by local name, or ``None``."""
    current = element
    for name in path:
        if current is None:
            return None
        current = next(_children(current, name), None)
    return current


def _text(element: ET.Element | None, *path: str) -> str | None:
    found = _child(element, *path)
    if found is None or found.text is None:
        return None
    stripped = " ".join(found.text.split())
    return stripped or None


# --- Values ------------------------------------------------------------------------


def _amount(element: ET.Element | None, *, where: str) -> tuple[Decimal, str]:
    """An ``Amt`` element as (amount, currency), checked for shape.

    ISO 20022 amounts are non-negative with the sign carried separately in
    ``CdtDbtInd``, and a krone amount has at most two decimals. Anything else
    is a file we would be guessing about.
    """
    if element is None or element.text is None:
        raise InvalidBankStatementError(f"{where} mangler beløp")
    try:
        value = Decimal(element.text.strip())
    except InvalidOperation as error:
        raise InvalidBankStatementError(f"{where} har et beløp som ikke er et tall") from error
    exponent = value.as_tuple().exponent
    if not value.is_finite() or not isinstance(exponent, int) or exponent < -2 or value < 0:
        raise InvalidBankStatementError(f"{where} har et ugyldig beløp: {element.text.strip()}")
    currency = (element.get("Ccy") or "").strip().upper()
    if not currency:
        raise InvalidBankStatementError(f"{where} mangler valuta")
    return value.quantize(Decimal("0.01")), currency


def _sign(element: ET.Element, *, where: str) -> CashFlowDirection:
    indicator = _text(element, "CdtDbtInd")
    if indicator == "CRDT":
        return CashFlowDirection.INN
    if indicator == "DBIT":
        return CashFlowDirection.UT
    raise InvalidBankStatementError(f"{where} sier ikke om beløpet er inn eller ut (CdtDbtInd)")


def _date(element: ET.Element | None) -> date | None:
    """``Dt`` or ``DtTm`` under ``element``, as a calendar date.

    A ``DtTm`` is cut to its date as written, not converted to Norwegian time:
    the bank wrote the booking day it meant, and moving a 00:30+01:00 booking to
    the day before would put it in the wrong period.
    """
    raw = _text(element, "Dt") or _text(element, "DtTm")
    if raw is None and element is not None and element.text and element.text.strip():
        raw = element.text.strip()
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as error:
        raise InvalidBankStatementError(f"ugyldig dato i kontoutskriften: {raw}") from error


def _account_number(statement: ET.Element) -> str:
    account = _child(statement, "Acct")
    raw = _text(account, "Id", "IBAN") or _text(account, "Id", "Othr", "Id")
    if raw is None:
        raise InvalidBankStatementError("kontoutskriften sier ikke hvilken konto den gjelder")
    try:
        return validate_bank_account(raw)
    except InvalidBankAccountError as error:
        raise InvalidBankStatementError(
            f"kontoutskriften gjelder konto {raw}, som ikke er et norsk kontonummer. "
            "Bare norske kontoer kan importeres ennå"
        ) from error


def _require_nok(currency: str, *, where: str) -> None:
    if currency != NOK:
        raise UnsupportedStatementCurrencyError(
            f"{where} er i {currency}. Myntos importerer bare kontoutskrifter i norske kroner "
            "ennå, og en utskrift uten valutalinjene ville ikke gått opp"
        )


def _balance(statement: ET.Element, codes: Iterable[str]) -> tuple[Decimal, date | None] | None:
    """The first balance of the first type in ``codes`` the statement carries."""
    balances = list(_children(statement, "Bal"))
    for code in codes:
        for balance in balances:
            type_code = _text(balance, "Tp", "CdOrPrtry", "Cd")
            if type_code != code:
                continue
            value, currency = _amount(_child(balance, "Amt"), where=f"Saldoen {code}")
            _require_nok(currency, where=f"Saldoen {code}")
            signed = (
                value
                if _sign(balance, where=f"Saldoen {code}") is CashFlowDirection.INN
                else -value
            )
            return signed, _date(_child(balance, "Dt"))
    return None


def _party_name(details: ET.Element, role: str) -> str | None:
    """A related party's name, in both the pre-2019 and the 2019 shape.

    camt.053.001.02 puts the name straight under the party (``Dbtr/Nm``); from
    the 2019 versions on the party is a choice between a person and an agent,
    and the name moved one level down (``Dbtr/Pty/Nm``). Reading both is what
    lets one parser take both versions.
    """
    parties = _child(details, "RltdPties")
    for candidate in (role, f"Ultmt{role}"):
        name = _text(parties, candidate, "Nm") or _text(parties, candidate, "Pty", "Nm")
        if name is not None:
            return name
    return None


def _entry(element: ET.Element, *, index: int) -> ParsedEntry | None:
    """One ``Ntry``, or ``None`` when it is not booked."""
    where = f"Postering {index}"
    status = _text(element, "Sts") or _text(element, "Sts", "Cd")
    if status is not None and status != _BOOKED:
        return None

    amount, currency = _amount(_child(element, "Amt"), where=where)
    _require_nok(currency, where=where)
    direction = _sign(element, where=where)

    booking_date = _date(_child(element, "BookgDt"))
    if booking_date is None:
        raise InvalidBankStatementError(f"{where} mangler bokføringsdato")
    value_date = _date(_child(element, "ValDt"))

    details = [tx for batch in _children(element, "NtryDtls") for tx in _children(batch, "TxDtls")]
    # Money in was paid by the debtor; money out went to the creditor. The other
    # party on the line is always the account holder, which says nothing.
    role = "Dbtr" if direction is CashFlowDirection.INN else "Cdtr"
    names = {name for name in (_party_name(tx, role) for tx in details) if name}
    # A batch booking (one entry, many transactions) has one counterparty only
    # when every transaction in it names the same one. Picking the first of
    # several would put one payer's name on everybody's money.
    counterparty = names.pop() if len(names) == 1 else None

    messages = [
        " ".join(text.split())
        for tx in details
        for info in _children(tx, "RmtInf")
        for unstructured in _children(info, "Ustrd")
        if (text := unstructured.text) and text.strip()
    ]
    kid = next(
        (
            ref
            for tx in details
            for info in _children(tx, "RmtInf")
            for structured in _children(info, "Strd")
            if (ref := _text(structured, "CdtrRefInf", "Ref")) is not None
        ),
        None,
    )
    end_to_end = next(
        (
            ref
            for tx in details
            if (ref := _text(tx, "Refs", "EndToEndId")) not in (None, "NOTPROVIDED")
        ),
        None,
    )
    # The entry's own reference first. Only when the entry has none and there
    # is exactly one transaction under it is that transaction's reference the
    # entry's: with several, any one of them names a part, not the booking.
    reference = _text(element, "AcctSvcrRef")
    if reference is None and len(details) == 1:
        reference = _text(details[0], "Refs", "AcctSvcrRef")

    return ParsedEntry(
        booking_date=booking_date,
        value_date=value_date,
        amount=amount,
        direction=direction,
        currency=currency,
        counterparty_name=counterparty,
        remittance=" ".join(messages) or None,
        kid=kid,
        end_to_end_id=end_to_end,
        bank_reference=reference,
        additional_info=_text(element, "AddtlNtryInf"),
        reversal=(_text(element, "RvslInd") or "").lower() == "true",
    )


def _check_summary(statement: ET.Element, entries: list[ParsedEntry], *, where: str) -> None:
    """The bank's own totals, when it sent them, must agree with its entries.

    Optional in the format, so only checked when present. When it is, it is the
    cheapest way to catch a file cut short at a line boundary, which still
    parses and whose last balance may have been cut off along with it.
    """
    summary = _child(statement, "TxsSummry")
    if summary is None:
        return
    for element_name, direction in (
        ("TtlCdtNtries", CashFlowDirection.INN),
        ("TtlDbtNtries", CashFlowDirection.UT),
    ):
        stated = _text(summary, element_name, "Sum")
        if stated is None:
            continue
        try:
            expected = Decimal(stated)
        except InvalidOperation as error:
            raise InvalidBankStatementError(f"{where}: ugyldig sum i TxsSummry") from error
        actual = sum((e.amount for e in entries if e.direction is direction), ZERO)
        if actual != expected:
            side = "innbetalinger" if direction is CashFlowDirection.INN else "utbetalinger"
            raise UnbalancedBankStatementError(
                f"{where}: banken oppgir {expected} kroner i {side}, men posteringene i filen "
                f"summerer til {actual}. Filen er trolig ufullstendig"
            )


def _statement(element: ET.Element, *, number: int) -> ParsedStatement:
    where = f"Kontoutskrift {number}"
    statement_id = _text(element, "Id") or f"utskrift-{number}"
    account_number = _account_number(element)
    account_currency = _text(element, "Acct", "Ccy")
    if account_currency is not None:
        _require_nok(account_currency.upper(), where=f"Konto {format_bank_account(account_number)}")

    opening = _balance(element, _OPENING_CODES)
    closing = _balance(element, (_CLOSING_CODE,))
    if opening is None:
        raise InvalidBankStatementError(f"{where} mangler inngående saldo (OPBD eller PRCD)")
    if closing is None:
        raise InvalidBankStatementError(f"{where} mangler utgående saldo (CLBD)")

    entries: list[ParsedEntry] = []
    skipped = 0
    for index, raw in enumerate(_children(element, "Ntry"), start=1):
        entry = _entry(raw, index=index)
        if entry is None:
            skipped += 1
            continue
        entries.append(entry)

    # The bank's summary counts every entry in the report, booked or not, so it
    # is only comparable when every entry was booked.
    if skipped == 0:
        _check_summary(element, entries, where=where)

    opening_balance, opening_date = opening
    closing_balance, closing_date = closing
    movement = sum(
        (e.amount if e.direction is CashFlowDirection.INN else -e.amount for e in entries), ZERO
    )
    if opening_balance + movement != closing_balance:
        raise UnbalancedBankStatementError(
            f"{where} går ikke opp: inngående saldo {opening_balance} pluss posteringene "
            f"({movement}) blir {opening_balance + movement}, men utgående saldo er "
            f"{closing_balance}. Filen er ikke importert"
        )

    period = _child(element, "FrToDt")
    from_date = _date(_child(period, "FrDtTm")) if period is not None else None
    to_date = _date(_child(period, "ToDtTm")) if period is not None else None
    if from_date is None and opening_date is not None:
        # PRCD is dated the day it closed, which is the day before this period.
        uses_previous_close = _balance(element, ("OPBD",)) is None
        from_date = opening_date + timedelta(days=1) if uses_previous_close else opening_date
    if to_date is None:
        to_date = closing_date
    booked = [e.booking_date for e in entries]
    from_date = from_date or (min(booked) if booked else None)
    to_date = to_date or (max(booked) if booked else None)
    if from_date is None or to_date is None:
        raise InvalidBankStatementError(f"{where} sier ikke hvilken periode den gjelder")
    if to_date < from_date:
        raise InvalidBankStatementError(f"{where} slutter før den begynner")

    return ParsedStatement(
        statement_id=statement_id[:128],
        account_number=account_number,
        from_date=from_date,
        to_date=to_date,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        entries=tuple(entries),
        skipped_unbooked=skipped,
    )


def parse(content: bytes) -> ParsedFile:
    """Read a camt.053 document, or raise saying what is wrong with it.

    All or nothing: one statement in the file that does not add up refuses the
    whole file, because the import is one request and a file half-imported is
    a file nobody can tell apart from one fully imported.
    """
    if not content.strip():
        raise InvalidBankStatementError("filen er tom")
    root = _parse_xml(content)
    match = _CAMT_NAMESPACE.match(_namespace(root.tag))
    if _local(root.tag) != "Document" or match is None:
        raise InvalidBankStatementError(
            "filen er ikke en ISO 20022-kontoutskrift (camt.053). Velg «camt.053» eller "
            "«ISO 20022 XML» når du laster ned fra nettbanken"
        )
    message, version = match.group(1), match.group(2)
    if message != _STATEMENT_MESSAGE:
        explained = _OTHER_MESSAGES.get(message, f"{message} er ikke en kontoutskrift")
        raise InvalidBankStatementError(
            f"filen er {message}, ikke camt.053: {explained}. Last ned kontoutskriften "
            "(camt.053) i stedet"
        )
    body = _child(root, "BkToCstmrStmt")
    statements = list(_children(body, "Stmt")) if body is not None else []
    if not statements:
        raise InvalidBankStatementError("filen inneholder ingen kontoutskrift")
    return ParsedFile(
        message_id=_text(body, "GrpHdr", "MsgId"),
        version=f"001.{version}",
        statements=tuple(
            _statement(element, number=number) for number, element in enumerate(statements, 1)
        ),
    )


# --- The dedup key -------------------------------------------------------------------


def dedup_keys(entries: Iterable[ParsedEntry]) -> list[str]:
    """The key each entry is stored under, in order.

    **The bank's reference when it gave one** (``ref:`` + ``AcctSvcrRef``). It
    is the bank's own identifier for the booking, unique per account, and it is
    the same in every file the booking appears in, whichever period the export
    covered. That makes it the one key under which a re-import and an
    overlapping import are both recognised for what they are.

    **A fingerprint when it did not** (``fp:`` + a digest + an ordinal). The
    fields the bank wrote about the movement itself, which an overlapping export
    repeats unchanged, hashed together. The ordinal is what keeps two genuinely
    identical bookings apart, two 49-krone card fees on the same day being the
    ordinary case: within one statement they are the first and the second of
    that fingerprint, and an overlapping statement covering the same day sees
    the same two and numbers them the same way, because a statement always
    covers whole booking days.

    What the fingerprint cannot do is tell a booking the bank silently changed
    from a new one; that is the price of a bank that sends no reference, and the
    reason the reference wins whenever it is there.
    """
    seen: dict[str, int] = {}
    keys: list[str] = []
    for entry in entries:
        if entry.bank_reference:
            keys.append(f"ref:{entry.bank_reference}"[:160])
            continue
        material = "|".join(
            (
                entry.booking_date.isoformat(),
                entry.value_date.isoformat() if entry.value_date else "",
                entry.direction.value,
                str(entry.amount),
                entry.currency,
                entry.counterparty_name or "",
                entry.remittance or "",
                entry.kid or "",
                entry.end_to_end_id or "",
                entry.additional_info or "",
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        seen[digest] = seen.get(digest, 0) + 1
        keys.append(f"fp:{digest}:{seen[digest]}")
    return keys
