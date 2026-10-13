"""The bilagsjournal: every posted voucher, from every journal.

``GET /companies/{id}/bilag``
    One page of a year's vouchers with their lines, narrowed by month, journal,
    account or text. Narrowed to an account, each voucher carries its movement
    on it and the balance after.

Why it exists and what it leaves to other pages is in
:mod:`myntos.domain.ledger.vouchers`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import vouchers

router = APIRouter(tags=["bilag"])


class VoucherLineResponse(BaseModel):
    account_number: str
    account_description: str
    description: str | None
    debit: Decimal
    credit: Decimal
    party_name: str | None
    tax_code: str | None
    #: «20 Storfe»: each avdeling, prosjekt or driftsgren the line carries.
    dimensions: list[str]


class VoucherResponse(BaseModel):
    id: int
    #: The journal's code: ``KONTANT`` (payments), ``SALG`` (invoices), ``MVA``
    #: (VAT settlements), ``IB`` (opening balance), ``IMPORT`` (from the previous
    #: program), ``MANUELT``, ``AVS`` (year-end) and the add-ons' own.
    journal: str
    journal_description: str
    voucher_no: str
    transaction_date: date
    description: str
    voucher_type: str | None
    source_document: str | None
    total: Decimal
    reverses_id: int | None
    reversed_by_id: int | None
    created_by_name: str | None
    lines: list[VoucherLineResponse]
    movement: Decimal | None
    balance: Decimal | None


class JournalCountResponse(BaseModel):
    code: str
    description: str
    vouchers: int


class VoucherPageResponse(BaseModel):
    company_id: int
    year: int
    month: int | None
    total_count: int
    offset: int
    vouchers: list[VoucherResponse]
    journals: list[JournalCountResponse]
    account_number: str | None
    account_description: str | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None


@router.get("/companies/{company_id}/bilag", openapi_extra=KEY_READ)
async def list_vouchers(
    context: CurrentCompany,
    db: DbSession,
    aar: Annotated[int, Query(ge=2000, le=2100)],
    maaned: Annotated[int | None, Query(ge=1, le=12)] = None,
    journal: Annotated[str | None, Query(max_length=16)] = None,
    konto: Annotated[str | None, Query(max_length=32)] = None,
    sok: Annotated[str | None, Query(max_length=100)] = None,
    fra: Annotated[int, Query(ge=0)] = 0,
) -> VoucherPageResponse:
    """The bilagsjournal: every posted voucher of a year, with its lines, oldest first.

    Everything in the books, whatever wrote it: payments registered in Myntos,
    invoices, VAT settlements, the opening balance (inngående balanse), the
    vouchers brought over from the previous accounting program (journal
    ``IMPORT``), manual vouchers and the year-end. The payments register
    (``…/cashflow``) lists only the first kind; this is where to see that an
    import came over, or what is on an account.

    ``aar`` is the year. Narrow it with ``maaned`` (1-12), ``journal`` (a code
    from ``journals`` in the answer, which counts each journal's vouchers in the
    year), ``konto`` (an account number: only vouchers with a line on it) and
    ``sok`` (text in the voucher's or a line's description, or its number). 100
    vouchers a page; ``fra`` is the offset, and ``total_count`` how many match.

    With ``konto``, each voucher has its ``movement`` on that account (debit
    positive) and the ``balance`` after it, from ``opening_balance`` (what the
    account held before the period; an income or cost account opens every year
    at zero) to ``closing_balance``. With ``konto=1920`` that is the bank's ins
    and outs. A voucher number is only read here; corrections are made where
    the voucher was made. Amounts are decimal strings. 404
    ``account_not_found`` for an account the kontoplan does not have. Needs
    ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    page = await vouchers.list_vouchers(
        db,
        company=context.company,
        year=aar,
        month=maaned,
        journal=journal,
        account_number=konto,
        search=sok,
        offset=fra,
    )
    return VoucherPageResponse(
        company_id=context.company.id,
        year=page.year,
        month=page.month,
        total_count=page.total_count,
        offset=page.offset,
        vouchers=[
            VoucherResponse(
                id=row.id,
                journal=row.journal,
                journal_description=row.journal_description,
                voucher_no=row.voucher_no,
                transaction_date=row.on,
                description=row.description,
                voucher_type=row.voucher_type,
                source_document=row.source_document,
                total=row.total,
                reverses_id=row.reverses_id,
                reversed_by_id=row.reversed_by_id,
                created_by_name=row.created_by_name,
                lines=[
                    VoucherLineResponse(
                        account_number=line.account_number,
                        account_description=line.account_description,
                        description=line.description,
                        debit=line.debit,
                        credit=line.credit,
                        party_name=line.party_name,
                        tax_code=line.tax_code,
                        dimensions=list(line.dimensions),
                    )
                    for line in row.lines
                ],
                movement=row.movement,
                balance=row.balance,
            )
            for row in page.vouchers
        ],
        journals=[
            JournalCountResponse(code=j.code, description=j.description, vouchers=j.vouchers)
            for j in page.journals
        ],
        account_number=page.account_number,
        account_description=page.account_description,
        opening_balance=page.opening_balance,
        closing_balance=page.closing_balance,
    )
