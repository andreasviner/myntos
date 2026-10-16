"""Resultat og balanse for every financial year.

``GET /companies/{id}/regnskap``
    The profit and loss account and the balance sheet, one entry per year,
    newest first, grouped the way an årsregnskap is laid out.

How the figures are read and why they are grouped by account class is in
:mod:`myntos.domain.ledger.report`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import report

router = APIRouter(tags=["regnskap"])


class AccountFigureResponse(BaseModel):
    account_number: str
    description: str
    amount: Decimal


class ReportLineResponse(BaseModel):
    #: Stable across years and releases: «driftsinntekter», «arsresultat», …
    key: str
    #: The bokmål the page shows: «Sum driftsinntekter», «Årsresultat».
    label: str
    #: ``gruppe`` sums accounts, which are listed; ``sum`` adds up lines above.
    kind: report.LineKind
    amount: Decimal
    accounts: list[AccountFigureResponse]


class YearFiguresResponse(BaseModel):
    year: int
    start_date: date
    end_date: date
    closed: bool
    voucher_count: int
    last_entry: date | None
    resultat: list[ReportLineResponse]
    balanse: list[ReportLineResponse]


class AccountsResponse(BaseModel):
    company_id: int
    years: list[YearFiguresResponse]


def _lines(lines: list[report.Line]) -> list[ReportLineResponse]:
    return [
        ReportLineResponse(
            key=line.key,
            label=line.label,
            kind=line.kind,
            amount=line.amount,
            accounts=[
                AccountFigureResponse(
                    account_number=a.account_number, description=a.description, amount=a.amount
                )
                for a in line.accounts
            ],
        )
        for line in lines
    ]


@router.get("/companies/{company_id}/regnskap", openapi_extra=KEY_READ)
async def accounts(context: CurrentCompany, db: DbSession) -> AccountsResponse:
    """The accounts for every year: resultatregnskap and balanse, side by side.

    One entry in ``years`` per financial year the company has in Myntos,
    newest first, including years brought over from a previous program
    (SAF-T import) and a year with nothing in it yet. Use it to answer «hva
    var resultatet i fjor?», «hvordan går det i år mot i fjor?», «hva har vi i
    egenkapital?», or to show a person the years their books cover.

    Each year has ``closed`` (avsluttet: the year-end close has been done),
    ``voucher_count`` and ``last_entry``, the date of its last voucher. A year
    whose ``last_entry`` is null has nothing posted: the figures are zero
    because the year has not been brought in or written, not because nothing
    happened.

    ``resultat`` is the profit and loss account for the year alone and
    ``balanse`` the balance sheet at the year's end (everything posted up to
    and including it). Both are ordered lists of lines, laid out as an
    årsregnskap is: a line of kind ``gruppe`` is the accounts of one class of
    the standard chart (NS 4102) summed, with ``accounts`` behind it; a line
    of kind ``sum`` adds up the lines above it and has no accounts. ``key`` is
    stable, so the same line can be found in every year; the years list the
    same lines in the same order, and a group with nothing in any year is left
    out of all of them.

    Signs are as the statement reads. In ``resultat``, income lines and cost
    lines are both positive in the ordinary case, and ``arsresultat`` (the
    year's result after tax) is positive for a profit. In ``balanse``, assets
    (eiendeler) and equity and liabilities (egenkapital og gjeld) are both
    positive, and ``eiendeler`` equals ``egenkapital_og_gjeld``.

    ``udisponert_resultat`` under equity is result no year-end close has
    moved to equity yet: the open years' results. It goes to zero once each
    year is closed (``myntos-arsavslutning``).

    These are the books' figures, read off the ledger: not a filed
    årsregnskap or skattemelding, and before the year-end close they have no
    tax cost unless the previous program booked one. Amounts are decimal
    strings. Every role may read this, and every key.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    years = await report.every_year(db, company=context.company)
    return AccountsResponse(
        company_id=context.company.id,
        years=[
            YearFiguresResponse(
                year=y.year,
                start_date=y.start_date,
                end_date=y.end_date,
                closed=y.closed,
                voucher_count=y.voucher_count,
                last_entry=y.last_entry,
                resultat=_lines(y.result_lines),
                balanse=_lines(y.balance_lines),
            )
            for y in years
        ],
    )
