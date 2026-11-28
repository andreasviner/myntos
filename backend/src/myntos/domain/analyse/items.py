"""Which vouchers KI reads, what it is shown of each, and what the software knows itself.

docs/analyse.md «What is read». An **item** is a posted voucher with at least
one line on an ``inntekt`` or ``kostnad`` account, outside the ``IB`` and
``AVS`` journals: the same scope as the figures, so a tag can split exactly
what the page sums. Its **lines** are those income and cost lines and no
others; the payment and VAT lines carry nothing a tag could say.

The canonical input is one JSON object per voucher, written here and nowhere
else, because three things must agree on it byte for byte: what the model is
shown, the hash in the stamp (a changed party name or a «Beskriv bilaget»
filled in later must count as a change), and the test that the prompt prefix
does not move. Field names are bokmål, as the model answers in bokmål.

Two kinds of voucher are never asked (rule 3, the software fills in what it
knows): a reversal is the mirror of its original, and a voucher in ``AVSKR``
is depreciation. Their rows say ``source = 'regnskap'``.
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.core.clock import as_utc
from myntos.domain.analyse.models import KiVoucherAnalysis
from myntos.domain.bilag.models import VoucherExplanation
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES
from myntos.domain.ledger.chart import (
    CLOSING_JOURNAL_ID,
    DEPRECIATION_JOURNAL_ID,
    OPENING_JOURNAL_ID,
)
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Journal,
    Transaction,
    TransactionLine,
)

#: Journals that are never items: the opening balance is last year's result
#: carried in, and the closing entries are the tax cost and the disposition.
#: Neither is income or cost of the period (docs/analyse.md «The figures»).
EXCLUDED_JOURNALS = (OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)

#: Bumped when the deterministic rules below change what they store, so the
#: rows they wrote count as changed and are written again.
RULES_VERSION = 1

# How much free text the model is shown. Enough to say what a voucher is;
# a pasted contract in a description is not more information, only more of
# the company's business leaving the building and more tokens.
_TEXT_LINE = 80
_TEXT_VOUCHER = 200
_TEXT_EXPLANATION = 300
_TEXT_NAME = 80

_INCOME_COST = (AccountType.INNTEKT, AccountType.KOSTNAD)

REVERSAL_SUMMARY = "Tilbakeføring av bilag {bilag}"
DEPRECIATION_SUMMARY = "Avskrivning av driftsmidler"


class ItemKind(enum.StrEnum):
    #: Asked, in a batch.
    KI = "ki"
    #: Mirrors its original's tags (``reverses_transaction_pk`` set).
    REVERSAL = "reversal"
    #: ``AVSKR``: depreciation, tagged without asking.
    DEPRECIATION = "depreciation"


@dataclass(frozen=True)
class ItemLine:
    """One income or cost line, numbered as the model sees it."""

    nr: int
    line_pk: int
    record_id: str
    account_number: str
    #: ``inntekt`` or ``kostnad``.
    retning: str
    #: On the line's natural side: a cost is debit minus credit, income credit
    #: minus debit. Negative is a refund or a credit note.
    amount: Decimal
    has_party: bool


@dataclass(frozen=True)
class Item:
    """One voucher in scope, with what the model would be shown of it."""

    transaction_pk: int
    bilag: str
    journal: str
    dato: date
    kind: ItemKind
    lines: tuple[ItemLine, ...]
    #: Whether any line of the voucher, income and cost or not, names a party.
    #: Then the books know the counterparty and a ``motpart`` tag is not asked.
    has_party: bool
    reverses_pk: int | None
    #: The object the model is shown, keys in this order.
    canonical: dict[str, Any] = field(hash=False, compare=False)

    @property
    def rendered(self) -> str:
        """The object as one line of JSON: what goes in the prompt and what is hashed."""
        return render(self.canonical)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def line(self, nr: int) -> ItemLine | None:
        for line in self.lines:
            if line.nr == nr:
                return line
        return None


def render(canonical: dict[str, Any]) -> str:
    """One object, compact and with its letters as they are (æøå, not escapes)."""
    return json.dumps(canonical, ensure_ascii=False, separators=(", ", ": "))


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def input_hash(item: Item) -> str:
    """The hash in the stamp of a voucher asked (or of depreciation).

    SHA-256 of exactly what the model is shown. Never with ``dokument``, the
    text read off the bilag that a later improvement may add: a run without it
    must not count as a change (docs/analyse.md «What is read»).
    """
    if item.kind is ItemKind.DEPRECIATION:
        return _digest("avskrivning", str(RULES_VERSION), item.rendered)
    return _digest(item.rendered)


def reversal_hash(item: Item, original: KiVoucherAnalysis | None) -> str:
    """The hash of a reversal: its own input and what its original's analysis is.

    The mirror is only as current as the original, so the original's stamp is
    part of the input: when the original is read again, the reversal counts as
    changed and is mirrored again.
    """
    if original is None:
        return _digest("tilbakeforing", str(RULES_VERSION), item.rendered, "uten-original")
    return _digest(
        "tilbakeforing",
        str(RULES_VERSION),
        item.rendered,
        original.input_hash,
        original.status,
        original.level,
        as_utc(original.analysed_at).isoformat(),
    )


def _cap(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    flat = " ".join(text.split())
    if not flat:
        return None
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _amount(line: TransactionLine, retning: str) -> Decimal:
    debit = line.debit or Decimal("0")
    credit = line.credit or Decimal("0")
    value = debit - credit if retning == AccountType.KOSTNAD.value else credit - debit
    return value.quantize(Decimal("0.01"))


def _item(transaction: Transaction, explanation: str | None) -> Item:
    journal = transaction.journal.journal_id
    ordered = sorted(transaction.lines, key=lambda line: line.id)
    party = next((line.party for line in ordered if line.party is not None), None)
    lines: list[ItemLine] = []
    shown: list[dict[str, Any]] = []
    for line in ordered:
        account = line.account
        if account.account_type not in _INCOME_COST:
            continue
        retning = AccountType(account.account_type).value
        amount = _amount(line, retning)
        nr = len(lines) + 1
        lines.append(
            ItemLine(
                nr=nr,
                line_pk=line.id,
                record_id=line.record_id,
                account_number=account.account_number,
                retning=retning,
                amount=amount,
                has_party=line.party_id is not None,
            )
        )
        shown.append(
            {
                "nr": nr,
                "konto": f"{account.account_number} {_cap(account.description, _TEXT_NAME)}",
                "retning": retning,
                "belop": f"{amount:.2f}",
                "mva": line.tax_code.code if line.tax_code is not None else None,
                "tekst": _cap(line.description, _TEXT_LINE),
            }
        )
    category = CASH_FLOW_TYPES.get(transaction.voucher_type or "")
    canonical: dict[str, Any] = {
        "bilag": transaction.transaction_id,
        "dato": transaction.transaction_date.isoformat(),
        "journal": journal,
        "kategori": category.label if category is not None else None,
        "beskrivelse": _cap(transaction.description, _TEXT_VOUCHER),
        "forklaring": _cap(explanation, _TEXT_EXPLANATION),
        "motpart": (
            {"navn": _cap(party.name, _TEXT_NAME), "type": str(party.party_type)}
            if party is not None
            else None
        ),
        "linjer": shown,
    }
    if transaction.reverses_transaction_pk is not None:
        kind = ItemKind.REVERSAL
    elif journal == DEPRECIATION_JOURNAL_ID:
        kind = ItemKind.DEPRECIATION
    else:
        kind = ItemKind.KI
    return Item(
        transaction_pk=transaction.id,
        bilag=transaction.transaction_id,
        journal=journal,
        dato=transaction.transaction_date,
        kind=kind,
        lines=tuple(lines),
        has_party=party is not None,
        reverses_pk=transaction.reverses_transaction_pk,
        canonical=canonical,
    )


def _has_income_or_cost() -> Any:
    return exists(
        select(TransactionLine.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            TransactionLine.transaction_pk == Transaction.id,
            Account.account_type.in_(_INCOME_COST),
        )
    )


def _in_scope(company_id: int) -> Any:
    return and_(
        Journal.company_id == company_id,
        Transaction.posted_at.is_not(None),
        Journal.journal_id.not_in(EXCLUDED_JOURNALS),
        _has_income_or_cost(),
    )


async def _load(db: AsyncSession, company_id: int, *where: Any) -> list[Item]:
    transactions = (
        await db.scalars(
            select(Transaction)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(_in_scope(company_id), *where)
            .options(
                selectinload(Transaction.journal),
                selectinload(Transaction.lines).selectinload(TransactionLine.account),
                selectinload(Transaction.lines).selectinload(TransactionLine.party),
                selectinload(Transaction.lines).selectinload(TransactionLine.tax_code),
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        )
    ).all()
    if not transactions:
        return []
    pks = [transaction.id for transaction in transactions]
    # A list first: a result has ``keys()``, so dict() would take it for a
    # mapping and index it by column name.
    explanations = dict(
        (
            await db.execute(
                select(VoucherExplanation.transaction_pk, VoucherExplanation.text).where(
                    VoucherExplanation.company_id == company_id,
                    VoucherExplanation.transaction_pk.in_(pks),
                )
            )
        )
        .tuples()
        .all()
    )
    return [_item(transaction, explanations.get(transaction.id)) for transaction in transactions]


async def in_period(db: AsyncSession, company_id: int, fra: date, til: date) -> list[Item]:
    """The company's items dated ``fra`` to ``til``, both included, newest first."""
    return await _load(
        db,
        company_id,
        Transaction.transaction_date >= fra,
        Transaction.transaction_date <= til,
    )


async def by_pk(db: AsyncSession, company_id: int, pks: Iterable[int]) -> list[Item]:
    """These vouchers, if they are the company's items: a reversal's originals."""
    wanted = sorted(set(pks))
    if not wanted:
        return []
    return await _load(db, company_id, Transaction.id.in_(wanted))


def comparison_of(fra: date, til: date) -> tuple[date, date]:
    """The same dates one year earlier («samme tid i fjor»); 29 February maps to 28."""
    return _year_earlier(fra), _year_earlier(til)


def _year_earlier(day: date) -> date:
    if day.month == 2 and day.day == 29:
        return date(day.year - 1, 2, 28)
    return day.replace(year=day.year - 1)


# --- What the software knows -------------------------------------------------------


@dataclass(frozen=True)
class OriginalLine:
    """A line of the voucher a reversal undoes, and the tags its analysis put on it."""

    record_id: str
    account_number: str
    amount: Decimal
    tag_ids: tuple[int, ...]


def mirror(item: Item, original: Sequence[OriginalLine]) -> dict[int, tuple[int, ...]]:
    """The tags for each line of a reversal, line pk to tag ids, from its original's.

    A reversal copies its original's lines with debit and credit swapped and
    the same ``record_id`` (:func:`myntos.domain.ledger.posting.build_reversal`),
    so a line is matched on that first; a voucher from elsewhere (an import)
    may not have kept it, so then on the account and the mirrored amount, then
    on the account alone. A line that matches nothing gets no tags here and
    ``annet`` from the caller, because every line has exactly one ``hva``.
    """
    tags: dict[int, tuple[int, ...]] = {}
    for line in item.lines:
        found = next(
            (
                candidate
                for candidate in original
                if candidate.record_id == line.record_id
                and candidate.account_number == line.account_number
            ),
            None,
        )
        if found is None:
            found = next(
                (
                    candidate
                    for candidate in original
                    if candidate.account_number == line.account_number
                    and candidate.amount == -line.amount
                ),
                None,
            )
        if found is None:
            found = next(
                (
                    candidate
                    for candidate in original
                    if candidate.account_number == line.account_number
                ),
                None,
            )
        tags[line.line_pk] = found.tag_ids if found is not None else ()
    return tags
