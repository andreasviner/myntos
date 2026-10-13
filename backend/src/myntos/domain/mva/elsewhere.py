"""Terms filed from another program: «Sendt fra et annet program».

A company that moves to Myntos in the middle of a year, or runs it beside its
old program for a while (the farm pilot's parallel run, docs/integrations/
saf-t.md), has terms the old program filed. Myntos computes those terms from
the vouchers the SAF-T import brought over and, knowing nothing else, offered
them as not sent. Sending one would not be a harmless duplicate: a second
melding for a term **replaces** the first at Skatteetaten, so Myntos's figures
would overwrite the old program's, and during a parallel run Myntos's books for
a term are often not yet complete.

So a person records that a term was filed elsewhere, with the day and,
optionally, the amount the other program filed. The term then:

- reads «Sendt fra et annet program» on the MVA page and in the API;
- is refused a check and a submission (409 ``filed_elsewhere``), and an agent's
  request to file it;
- is not overdue;
- shows Myntos's own figure beside the amount recorded, so a parallel run sees
  at once whether the two programs agree on the term.

It is a statement and not a filing, so it can be taken back
(:func:`withdraw`). That is also how a correction for such a term is filed from
Myntos once the move is complete: take the statement back, check, send.

**Proposed from the import.** The old program's own settlement of a term (its
2700 and 2710 moved onto 2740 when it filed) comes across in the ``IMPORT``
journal with the settlement's voucher type (:mod:`myntos.saft.voucher_import`).
:func:`proposals` matches each such voucher to the term it settles: the term
whose melding in Myntos has exactly the output and input VAT the voucher moved,
ending on or before the voucher's date and no more than
:data:`_SETTLEMENT_WINDOW` before it, the term ending on the voucher's day
first. Tripletex dates the voucher on the term's last day; a program that dates
it on the day it filed is still within the window. With one melding a voucher
dated on a term's last day is proposed for it even when the amounts differ,
because then the difference is the thing to look at. A proposal is shown, never
recorded on its own: the statement is the person's.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from myntos.core.clock import utc_now
from myntos.domain.ledger.chart import (
    IMPORT_JOURNAL_ID,
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    SETTLEMENT_JOURNAL_ID,
    SETTLEMENT_VOUCHER_TYPE,
)
from myntos.domain.ledger.errors import VoucherNotFoundError
from myntos.domain.ledger.models import Account, Company, Journal, Transaction, TransactionLine
from myntos.domain.mva import naering as naering_module
from myntos.domain.mva import service
from myntos.domain.mva.errors import (
    AlreadyFiledError,
    FiledElsewhereError,
    MeldingNotBuildableError,
    NotFiledElsewhereError,
)
from myntos.domain.mva.melding import Melding
from myntos.domain.mva.models import FilingStatus, MvaFiling, MvaFilingElsewhere
from myntos.domain.mva.periods import Termin

ZERO = Decimal("0.00")
_SETTLEMENT_ACCOUNT = "2740"

#: How long after a term's end a settlement voucher may still be that term's.
#: The latest deadline is a yearly primærnæring melding's, three months and ten
#: days (skatteforvaltningsforskriften § 8-3-10 (3)); a program that books the
#: settlement when it files is inside that, with a fortnight to spare for a
#: deadline moved off a weekend or a melding sent a little late.
_SETTLEMENT_WINDOW = timedelta(days=115)

#: Programs that round the settlement to whole kroner (Tripletex books the øre
#: on 7740) record a figure that differs from the melding's by less than a
#: krone. Anything a krone or more apart is a real difference.
_ROUNDING = Decimal("1.00")


def agrees(computed: Decimal, recorded: Decimal | None) -> bool | None:
    """Whether Myntos's figure and the recorded one are the same, to the krone.

    ``None`` when nothing was recorded: there is nothing to agree with.
    """
    if recorded is None:
        return None
    return abs(computed - recorded) < _ROUNDING


# --- Recording and taking back -------------------------------------------------------


async def _settlement_voucher(db: AsyncSession, company: Company, voucher_id: int) -> Transaction:
    """The old program's settlement the statement is made from, looked up through the company.

    Only a settlement voucher, and never one of Myntos's own (journal ``MVA``):
    those exist because Myntos filed the term, which is the opposite statement.
    """
    voucher = await db.scalar(
        select(Transaction)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Transaction.id == voucher_id,
            Journal.company_id == company.id,
            Journal.journal_id != SETTLEMENT_JOURNAL_ID,
            Transaction.voucher_type == SETTLEMENT_VOUCHER_TYPE,
            Transaction.posted_at.is_not(None),
        )
    )
    if voucher is None:
        raise VoucherNotFoundError(
            "finner ikke noe mva-oppgjør fra det gamle programmet med det bilaget"
        )
    return voucher


async def record(
    db: AsyncSession,
    *,
    company: Company,
    naering: naering_module.Naering,
    setup: naering_module.Naeringer,
    termin: Termin,
    filed_on: date,
    amount: Decimal | None,
    voucher_id: int | None,
    user_id: int | None,
    key_id: int | None,
) -> MvaFiling:
    """Record that ``termin`` of ``naering``'s melding was filed from another program.

    The caller has already refused a term that is not over and a company that
    is not VAT-registered, as for a filing. Refused here: a term Myntos filed
    (409 ``already_filed``: the statement would be false) and one already
    recorded (409 ``filed_elsewhere``).
    """
    filings = await service.filings_for(db, company, termin, naering, setup)
    if service.latest_filed(filings) is not None:
        raise AlreadyFiledError(
            f"{termin.label} er sendt fra Myntos, så den kan ikke merkes som sendt fra et annet "
            "program"
        )
    if service.standing_elsewhere(filings) is not None:
        raise FiledElsewhereError(
            f"{termin.label} er allerede merket som sendt fra et annet program"
        )
    voucher = await _settlement_voucher(db, company, voucher_id) if voucher_id is not None else None

    # Myntos's own figures for the term as they stand now, kept with the
    # statement: what the other program filed is compared with what Myntos
    # has, and the books may move after. A term Myntos cannot build (a code
    # the melding cannot carry) is still a term somebody filed elsewhere.
    melding: Melding | None
    try:
        melding = await service.build_melding(db, company, termin, naering, setup)
    except MeldingNotBuildableError:
        melding = None
    filing = MvaFiling(
        company_id=company.id,
        year=termin.year,
        period_type=termin.period_type.value,
        period_index=termin.index,
        category=naering.category.value,
        status=FilingStatus.ANNET_PROGRAM.value,
        reference=str(uuid.uuid4()),
        # Nothing was sent from here, so there is no melding and no answer to
        # keep. The routes that serve them refuse such a row by its status.
        melding_xml="",
        content_hash=service.content_hash(melding) if melding is not None else "",
        fastsatt_merverdiavgift=melding.fastsatt_merverdiavgift if melding is not None else ZERO,
        lines=service.lines_snapshot(melding) if melding is not None else [],
        merknad=None,
        validation_status="",
        validation_xml="",
        validated_at=utc_now(),
        created_by_user_id=user_id,
        documents=[],
        elsewhere=MvaFilingElsewhere(
            filed_on=filed_on,
            amount=amount,
            voucher_pk=voucher.id if voucher is not None else None,
            created_via_agent_key_id=key_id,
        ),
    )
    db.add(filing)
    await db.flush()
    return filing


async def withdraw(
    db: AsyncSession, filing: MvaFiling, *, user_id: int | None, key_id: int | None
) -> MvaFiling:
    """Take a statement back. The row stays, ``opphevet``, with who and when."""
    if filing.status != FilingStatus.ANNET_PROGRAM.value or filing.elsewhere is None:
        raise NotFiledElsewhereError(
            "bare en termin som er merket som sendt fra et annet program, kan angres"
        )
    filing.status = FilingStatus.OPPHEVET.value
    filing.elsewhere.withdrawn_at = utc_now()
    filing.elsewhere.withdrawn_by_user_id = user_id
    filing.elsewhere.withdrawn_via_agent_key_id = key_id
    await db.flush()
    return filing


# --- Proposals from the import -------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """An imported settlement voucher, and the term it looks like it settles."""

    voucher_pk: int
    voucher_no: str
    on: date
    description: str
    # What the old program moved: output VAT off 2700, input VAT off 2710, and
    # the net onto 2740, which is what it filed (perhaps rounded to kroner).
    output: Decimal
    input: Decimal
    amount: Decimal
    # Whether the term's melding in Myntos has exactly that output and input.
    matches: bool


async def _imported_settlements(
    db: AsyncSession, company: Company, start: date, end: date
) -> list[Proposal]:
    reversal = aliased(Transaction, name="cancelling_voucher")
    vouchers = list(
        await db.scalars(
            select(Transaction)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Journal.journal_id == IMPORT_JOURNAL_ID,
                Transaction.voucher_type == SETTLEMENT_VOUCHER_TYPE,
                Transaction.posted_at.is_not(None),
                Transaction.gl_posting_date >= start,
                Transaction.gl_posting_date <= end,
                Transaction.reverses_transaction_pk.is_(None),
                ~select(reversal.id)
                .where(reversal.reverses_transaction_pk == Transaction.id)
                .exists(),
            )
            .order_by(Transaction.gl_posting_date, Transaction.id)
        )
    )
    if not vouchers:
        return []
    moved: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    rows = await db.execute(
        select(
            TransactionLine.transaction_pk,
            Account.account_number,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            Account.company_id == company.id,
            TransactionLine.transaction_pk.in_([v.id for v in vouchers]),
            Account.account_number.in_(
                (OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT, _SETTLEMENT_ACCOUNT)
            ),
        )
        .group_by(TransactionLine.transaction_pk, Account.account_number)
    )
    for pk, number, debit, credit in rows:
        moved[pk][str(number)] += (debit or ZERO) - (credit or ZERO)
    return [
        Proposal(
            voucher_pk=voucher.id,
            voucher_no=voucher.transaction_id,
            on=voucher.gl_posting_date,
            description=voucher.description,
            output=moved[voucher.id][OUTPUT_VAT_ACCOUNT],
            input=-moved[voucher.id][INPUT_VAT_ACCOUNT],
            amount=-moved[voucher.id][_SETTLEMENT_ACCOUNT],
            matches=False,
        )
        for voucher in vouchers
    ]


async def _used_vouchers(db: AsyncSession, company: Company) -> set[int]:
    """Imported settlements a standing statement was already made from."""
    return {
        pk
        for pk in await db.scalars(
            select(MvaFilingElsewhere.voucher_pk)
            .join(MvaFiling, MvaFiling.id == MvaFilingElsewhere.filing_id)
            .where(
                MvaFiling.company_id == company.id,
                MvaFiling.status == FilingStatus.ANNET_PROGRAM.value,
                MvaFilingElsewhere.voucher_pk.is_not(None),
            )
        )
        if pk is not None
    }


async def proposals(
    db: AsyncSession,
    company: Company,
    summaries: Sequence[service.PeriodSummary],
    setup: naering_module.Naeringer,
) -> dict[int, Proposal]:
    """``{term index: proposal}`` for the open terms of one melding's year.

    ``summaries`` is the year as :func:`myntos.domain.mva.service.year_overview`
    built it, so the meldinger are not built twice.
    """
    open_terms = [s for s in summaries if not s.closed]
    if not open_terms:
        return {}
    start = min(s.termin.end for s in open_terms)
    end = max(s.termin.end for s in open_terms) + _SETTLEMENT_WINDOW
    used = await _used_vouchers(db, company)
    found: dict[int, Proposal] = {}
    for voucher in await _imported_settlements(db, company, start, end):
        if voucher.voucher_pk in used:
            continue
        candidates = [
            s
            for s in open_terms
            if s.termin.index not in found
            and s.termin.end <= voucher.on <= s.termin.end + _SETTLEMENT_WINDOW
        ]
        exact = [
            s
            for s in candidates
            if (s.melding.output_vat, s.melding.input_vat) == (voucher.output, voucher.input)
        ]
        if exact:
            chosen = min(
                exact, key=lambda s: (s.termin.end != voucher.on, voucher.on - s.termin.end)
            )
            found[chosen.termin.index] = Proposal(**{**voucher.__dict__, "matches": True})
            continue
        # With two meldinger a voucher whose amounts match neither is not
        # anybody's to guess: the day alone cannot say which melding it settled.
        if setup.is_split:
            continue
        same_day = [s for s in candidates if s.termin.end == voucher.on]
        if same_day:
            found[same_day[0].termin.index] = voucher
    return found
