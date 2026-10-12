"""Which posted voucher a bank line is most likely the booking of.

Pure functions, like :mod:`.camt`: the service fetches the candidates and this
module ranks them, so the rule can be read and tested without a database.

**A suggestion is never a match.** Nothing here writes anything. The register
is the record, and a voucher becomes "the booking of this bank line" only when a
person confirms it — the same rule that keeps a model's proposal off the ledger
until somebody has looked at it. A ranked list is the most this can honestly
offer, because the evidence is circumstantial: the same amount, a date nearby,
a name that resembles another name.

**The amount and the direction are requirements, not points.** A voucher that
moved 1 249 kroner is not a weaker match for a 1 250 krone bank line; it is a
different payment, or the same payment booked wrong, and either way confirming
it would mark the account reconciled while the books are a krone off. Only
candidates that pass both are scored at all.

The rest is points, and each one comes with the sentence that earned it, so the
interface can say *why* rather than show a number nobody can check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from myntos.domain.ledger.cashflow import CashFlowDirection

# How far a voucher's date may sit from the bank's booking date. Lopsided on
# purpose: a card purchase is entered on the day of the receipt and booked by
# the bank one to five working days later, so the voucher is usually *earlier*.
# A voucher dated well after the money moved is the unusual case.
WINDOW_BEFORE = timedelta(days=14)
WINDOW_AFTER = timedelta(days=7)

# At or above this a suggestion is shown as likely; below it, as possible. The
# interface says which, and offers the confirm button on both: the person
# decides, and a threshold that hid the weaker ones would decide for them.
STRONG = 70

# Words that say what kind of company something is and nothing about which one.
# "Kontorland AS" and "KONTORLAND NORGE AS" are the same supplier; "AS" in
# common is not evidence that two names are.
_NOISE = frozenset(
    {
        "as",
        "asa",
        "ans",
        "da",
        "enk",
        "nuf",
        "sa",
        "ab",
        "ltd",
        "limited",
        "gmbh",
        "inc",
        "llc",
        "norge",
        "norway",
        "og",
        "and",
        "the",
        "betaling",
        "varekjop",
        "varekjøp",
        "overforing",
        "overføring",
        "giro",
        "nettgiro",
        "kort",
        "faktura",
    }
)
_WORD = re.compile(r"[0-9a-zæøå]+")


@dataclass(frozen=True)
class LineFacts:
    """What the bank says about one movement — the half of the match we trust."""

    booking_date: date
    amount: Decimal
    direction: CashFlowDirection
    counterparty_name: str | None
    remittance: str | None
    kid: str | None
    additional_info: str | None


@dataclass(frozen=True)
class Candidate:
    """A posted voucher read back as a payment through the bank account."""

    voucher_id: int
    voucher_no: str
    occurred_on: date
    gross_amount: Decimal
    direction: CashFlowDirection
    description: str
    party_name: str | None
    type_code: str | None


@dataclass(frozen=True)
class Suggestion:
    candidate: Candidate
    score: int
    reasons: tuple[str, ...]

    @property
    def strong(self) -> bool:
        return self.score >= STRONG


def words(text: str | None) -> set[str]:
    """The words in ``text`` that could tell one counterparty from another."""
    if not text:
        return set()
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _NOISE}


def in_window(line: LineFacts, on: date) -> bool:
    return line.booking_date - WINDOW_BEFORE <= on <= line.booking_date + WINDOW_AFTER


def score(line: LineFacts, candidate: Candidate) -> Suggestion | None:
    """Rank one candidate against one line, or ``None`` if it cannot be the one."""
    if candidate.direction is not line.direction or candidate.gross_amount != line.amount:
        return None
    if not in_window(line, candidate.occurred_on):
        return None

    points = 30
    reasons = ["Samme beløp og retning"]

    days = abs((candidate.occurred_on - line.booking_date).days)
    if days == 0:
        points += 30
        reasons.append("Samme dato")
    elif days <= 3:
        points += 20
        reasons.append(f"{days} dag{'er' if days > 1 else ''} fra bokføringsdatoen i banken")
    elif days <= 7:
        points += 10
        reasons.append(f"{days} dager fra bokføringsdatoen i banken")

    entered = words(candidate.description) | words(candidate.party_name)
    bank_name = words(line.counterparty_name)
    if bank_name and bank_name & entered:
        points += 30
        reasons.append("Navnet i banken stemmer med bilaget")
    else:
        # The bank's free text is where a card purchase names the shop, and the
        # payer's message is where a customer names the invoice. Weaker than a
        # name the bank put in the counterparty field, because both are prose.
        said = words(line.remittance) | words(line.additional_info)
        if said & entered:
            points += 15
            reasons.append("Teksten i banken går igjen i bilaget")

    if line.kid and line.kid in candidate.description:
        points += 30
        reasons.append("KID-en står i beskrivelsen")

    return Suggestion(candidate=candidate, score=min(points, 100), reasons=tuple(reasons))


def rank(line: LineFacts, candidates: list[Candidate], *, limit: int = 3) -> list[Suggestion]:
    """The best few candidates for ``line``, best first.

    Ties go to the voucher closest in date and then to the older id, so the
    order is stable between two loads of the same page; a list that reshuffled
    itself would make the person wonder what had changed.
    """
    scored = [s for s in (score(line, c) for c in candidates) if s is not None]
    scored.sort(
        key=lambda s: (
            -s.score,
            abs((s.candidate.occurred_on - line.booking_date).days),
            s.candidate.voucher_id,
        )
    )
    return scored[:limit]
