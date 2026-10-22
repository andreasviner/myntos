"""Inngående balanse and årsavslutning.

Both flows are review-then-post, like the invoice: the page asks the backend
what would be posted and what is wrong with it, shows that, and only then
offers the button. The check runs again on the post, so the button and the rule
cannot disagree.

``GET  /companies/{id}/inngaende-balanse``
    The year an opening balance would go into, whether it may, what stands.
``POST /companies/{id}/inngaende-balanse/kontroll``
    The review of a set of lines. Posts nothing.
``POST /companies/{id}/inngaende-balanse/saldobalanse``
    A saldobalanse CSV read into lines, and reviewed. Posts nothing.
``POST /companies/{id}/inngaende-balanse/saft``
    The previous program's SAF-T file read into lines, with the accounts and
    parties the books are missing, and reviewed. Posts nothing.
``POST /companies/{id}/inngaende-balanse``
    Post it; with ``korriger`` reverse the standing one and post the replacement.
``POST /companies/{id}/inngaende-balanse/reversering``
    Withdraw the standing one.
``GET  /companies/{id}/regnskapsaar``
    Every year, open or closed, with the log of closes and reopenings.
``GET  /companies/{id}/arsavslutning?aar=&skattekostnad=``
    The closing plan: result, the vouchers it would post, findings.
``POST /companies/{id}/arsavslutning``
    Close the year.
``POST /companies/{id}/arsavslutning/gjenapning``
    Reopen it, with a reason.
``POST /companies/{id}/arsavslutning/opplosning``
    End the books on a dissolved company's last day.
``POST /companies/{id}/arsavslutning/opplosning/angre``
    Let them run to 31 December again.

No second id appears in any path: the year is a query or body field and is
always looked up through the company, so there is nothing to smuggle across the
company wall that ``get_company_context`` has not already settled.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    key_post,
)
from myntos.core.clock import utc_now
from myntos.domain.arsoppgjor import closing, opening, winding_up
from myntos.domain.arsoppgjor.errors import (
    InvalidOpeningBalanceError,
    YearNotClosableError,
)
from myntos.domain.arsoppgjor.findings import Finding
from myntos.domain.arsoppgjor.saldobalanse import MAX_BYTES
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger.models import AccountType, PartyType, Transaction
from myntos.saft import opening_import, reader
from myntos.saft.errors import InvalidSaftFileError

router = APIRouter(tags=["arsoppgjor"])

# A year ends at midnight in Norway, not in UTC.
_OSLO = ZoneInfo("Europe/Oslo")


def _today() -> date:
    return utc_now().astimezone(_OSLO).date()


# --- Shapes ------------------------------------------------------------------------


class FindingResponse(BaseModel):
    severity: str
    code: str
    message: str


def _findings(findings: list[Finding]) -> list[FindingResponse]:
    return [
        FindingResponse(severity=f.severity.value, code=f.code, message=f.message) for f in findings
    ]


def _refused(detail: str, code: str, findings: list[Finding]) -> JSONResponse:
    # Answered here rather than raised, as the SAF-T download does: what the
    # person needs is the list, not the first item of it.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": detail,
            "code": code,
            "findings": [f.model_dump() for f in _findings(findings)],
        },
    )


class OpeningLineRequest(BaseModel):
    account_number: str = Field(min_length=1, max_length=32)
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")
    party_id: int | None = None
    description: str | None = Field(default=None, max_length=255)

    def to_domain(self) -> opening.OpeningLineInput:
        return opening.OpeningLineInput(
            account_number=self.account_number.strip(),
            debit=self.debit,
            credit=self.credit,
            party_pk=self.party_id,
            description=self.description,
        )


class OpeningLinesRequest(BaseModel):
    lines: list[OpeningLineRequest] = Field(max_length=2000)


class PostOpeningRequest(OpeningLinesRequest):
    # Replacing the standing opening balance is something the person has to
    # mean; without it a second post is refused rather than doubling every
    # balance.
    korriger: bool = False


class OpeningLineResponse(BaseModel):
    account_number: str
    account_description: str | None
    debit: Decimal
    credit: Decimal
    party_id: int | None
    party_name: str | None
    description: str | None


def _line(line: opening.ReviewLine) -> OpeningLineResponse:
    return OpeningLineResponse(
        account_number=line.account_number,
        account_description=line.account_description,
        debit=line.debit,
        credit=line.credit,
        party_id=line.party_pk,
        party_name=line.party_name,
        description=line.description,
    )


class OpeningReviewResponse(BaseModel):
    company_id: int
    year: int | None
    lines: list[OpeningLineResponse]
    total_debit: Decimal
    total_credit: Decimal
    difference: Decimal
    postable: bool
    findings: list[FindingResponse]


def _review(company_id: int, checked: opening.OpeningReview) -> OpeningReviewResponse:
    return OpeningReviewResponse(
        company_id=company_id,
        year=checked.year,
        lines=[_line(line) for line in checked.lines],
        total_debit=checked.total_debit,
        total_credit=checked.total_credit,
        difference=checked.difference,
        postable=checked.postable,
        findings=_findings(checked.findings),
    )


class OpeningVoucherResponse(BaseModel):
    id: int
    voucher_no: str
    transaction_date: date
    posted_at: datetime | None
    is_reversal: bool
    reversed: bool
    corrects_id: int | None
    total: Decimal
    lines: list[OpeningLineResponse]


def _voucher(v: opening.OpeningVoucher) -> OpeningVoucherResponse:
    return OpeningVoucherResponse(
        id=v.id,
        voucher_no=v.voucher_no,
        transaction_date=v.transaction_date,
        posted_at=v.posted_at,
        is_reversal=v.is_reversal,
        reversed=v.reversed,
        corrects_id=v.corrects_id,
        total=v.total,
        lines=[_line(line) for line in v.lines],
    )


class OpeningStateResponse(BaseModel):
    company_id: int
    year: int | None
    allowed: bool
    refusal: str | None
    standing: OpeningVoucherResponse | None
    history: list[OpeningVoucherResponse]


class PostedVoucherResponse(BaseModel):
    company_id: int
    id: int
    voucher_no: str
    transaction_date: date
    description: str
    total: Decimal


def _posted(company_id: int, transaction: Transaction) -> PostedVoucherResponse:
    return PostedVoucherResponse(
        company_id=company_id,
        id=transaction.id,
        voucher_no=transaction.transaction_id,
        transaction_date=transaction.transaction_date,
        description=transaction.description,
        total=sum((line.debit for line in transaction.lines), Decimal("0.00")),
    )


# --- Inngående balanse -------------------------------------------------------------


@router.get("/companies/{company_id}/inngaende-balanse", openapi_extra=KEY_READ)
async def opening_state(context: CurrentCompany, db: DbSession) -> OpeningStateResponse:
    """Where the opening balance (inngående balanse) stands, and whether one may be posted.

    An opening balance is the balance sheet a company brings with it when its
    earlier years were kept in another accounting system: bank, receivables,
    payables, share capital, equity. Myntos posts it as one balanced voucher in
    its own journal (``IB``), dated the first day of the company's first
    financial year in Myntos. Read this before offering to enter one.

    - ``year``: the year it goes into, always the company's first in Myntos.
      Every later year opens with the year before's closing balance, derived
      from the postings, so there is nothing to enter for it.
    - ``allowed`` and ``refusal``: false, with the reason in Norwegian, when
      nothing may be posted now: the company has no financial year yet, or the
      first year is closed (reopen it under ``/arsavslutning/gjenapning``).
    - ``standing``: the opening voucher that counts now, or null.
    - ``history``: every opening voucher and reversal, oldest first. A posted
      voucher is never edited or deleted (bokføringsloven), so a corrected
      opening balance leaves the old one, its reversal and the replacement here.

    Needs read access to the books.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await opening.state(db, company=context.company)
    return OpeningStateResponse(
        company_id=context.company.id,
        year=found.year,
        allowed=found.refusal is None,
        refusal=found.refusal,
        standing=_voucher(found.standing) if found.standing is not None else None,
        history=[_voucher(v) for v in found.history],
    )


@router.post("/companies/{company_id}/inngaende-balanse/kontroll", openapi_extra=KEY_PREVIEW)
async def review_opening(
    payload: OpeningLinesRequest, context: CurrentCompany, db: DbSession
) -> OpeningReviewResponse:
    """Check a set of opening-balance lines without posting or storing anything.

    Send the lines exactly as they would be posted. Per line: an
    ``account_number`` from the company's chart of accounts (kontoplan); one
    amount, in ``debit`` or ``credit`` as a decimal string, never both;
    optionally ``party_id``, the customer or supplier an unpaid invoice on 1500
    or 2400 belongs to; and a ``description``. The answer is what
    ``POST /inngaende-balanse`` would post, each line resolved to its account
    and party, with the totals and ``findings``. The page shows this, not a sum
    of its own, because the backend is the one that decides.

    A finding with severity ``feil`` blocks posting (``postable`` is false); a
    ``merknad`` is worth reading and may well be right. The codes:

    - ``ubalanse``: debit and credit differ. A balance sheet always balances,
      so an account is missing or on the wrong side.
    - ``resultatkonto``: an income or cost account. An opening balance holds
      balance-sheet accounts only, because last year's result must already be
      on equity; the fix is the saldobalanse taken after the year-end close.
    - ``mva_konto``: 2700 or 2710. VAT not settled at the turn of the year goes
      on 2740, where its payment clears it; on 2700/2710 it would be swept into
      the first VAT return in Myntos.
    - ``ukjent_konto``: not in the kontoplan. Add it there, or use the account
      it belongs with.
    - ``ukjent_part`` and ``part_paa_feil_konto``: the customer or supplier is
      not this company's, or settles against another account.
    - ``reskontro_ufullstendig``: an account partly split per customer or
      supplier. All or nothing, since half a sub-ledger cannot be reconciled.
    - ``ugyldig_belop``, ``ingen_linjer``, ``aar_ikke_tillatt``: a bad amount,
      no lines, or a year that cannot take an opening balance now.
    - ``uten_aapne_poster`` (merknad): 1500 or 2400 not split per party.
      Allowed, but then the SAF-T file has no opening balance per party.

    Messages are Norwegian, for the person, and may quote account and party
    names from the books; decide from ``code``. Needs ``ledger:write`` (a
    member or above). For an API key this is a ``write`` that stores nothing,
    so it runs at once even when the key's writes wait for approval.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    checked = await opening.review(
        db, company=context.company, lines=[line.to_domain() for line in payload.lines]
    )
    return _review(context.company.id, checked)


class SaldobalanseResponse(OpeningReviewResponse):
    pass


@router.post("/companies/{company_id}/inngaende-balanse/saldobalanse", openapi_extra=KEY_PREVIEW)
async def read_saldobalanse(
    context: CurrentCompany,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> SaldobalanseResponse:
    """A saldobalanse read into lines for the form. Nothing is posted or stored.

    A saldobalanse (trial balance) is the list of every account's balance that
    the previous accounting system exports; taken after its year-end close, it
    is this company's opening balance. Upload it as ``multipart/form-data`` in
    the field ``file``: CSV, at most 2 MB, first row headings, one of them
    «Konto» or «Kontonr», and the balance either in one signed column
    («Utgående saldo», «UB» or «Saldo»; positive is debit) or in a «Debet» and
    «Kredit» pair. The answer is the same review as ``/kontroll``, with the
    lines to send on to it; ``party_id`` is never filled, since the file does
    not say which customer an open item belongs to. 422
    ``invalid_saldobalanse`` names the line or column it could not read.

    The file is not kept: it is a CSV export, not the bilag. The documentation
    of the opening balance is the previous year's approved årsregnskap and
    saldobalanse, which belong in the archive as the documents they are.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    content = await file.read(MAX_BYTES + 1)
    _, checked = await opening.from_saldobalanse(db, company=context.company, content=content)
    response = _review(context.company.id, checked)
    return SaldobalanseResponse(**response.model_dump())


class SaftSourceResponse(BaseModel):
    software: str | None
    audit_file_version: str
    orgnr: str
    company_name: str
    start_date: date
    end_date: date
    balances: str


class AccountToAddResponse(BaseModel):
    number: str
    description: str
    account_type: AccountType
    grouping_category: str
    grouping_code: str
    source_accounts: list[str]


class PartyToAddResponse(BaseModel):
    party_type: PartyType
    name: str
    orgnr: str | None
    party_id: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    source_id: str


class SaftOpeningResponse(OpeningReviewResponse):
    source: SaftSourceResponse
    accounts_to_add: list[AccountToAddResponse]
    parties_to_add: list[PartyToAddResponse]


@router.post("/companies/{company_id}/inngaende-balanse/saft", openapi_extra=KEY_PREVIEW)
async def read_saft(
    context: CurrentCompany,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> SaftOpeningResponse:
    """The previous program's SAF-T file read into opening-balance lines. Stores nothing.

    SAF-T Financial is the standard export every Norwegian accounting program
    writes (Tripletex, Fiken, PowerOffice, Visma …): the chart of accounts with
    each account's opening and closing balance, the customers and suppliers
    with theirs, and the vouchers. Upload it as ``multipart/form-data`` in the
    field ``file``, as the XML or as the ZIP it is downloaded in, at most
    200 MB. Only the balances are read; the vouchers are not imported.

    The file must be this company's (its organisasjonsnummer), else 422
    ``saft_wrong_company``. It must cover the company's first financial year in
    Myntos, whose opening balances are read, or the whole year before it, whose
    closing balances are; else 422 ``saft_wrong_year``, which names the file to
    export. 422 ``invalid_saft_file`` when it is not a SAF-T file that can be
    read.

    The answer is the review ``/kontroll`` gives, for the lines below, plus:

    - ``source``: what the file says it is, and ``balances``: ``inngaende`` or
      ``utgaende``, which of its balances were used.
    - ``accounts_to_add``: accounts the file has balances on that the kontoplan
      does not. Each balance lands on the first four digits of the file's
      account id (Tripletex writes department and product codes after them),
      named and grouped as the file has it, or by Myntos's default when the
      file's grouping is not in Skatteetaten's list or does not fit the
      account's class. Create them with ``POST …/kontoplan``.
    - ``parties_to_add``: customers and suppliers with open items that Myntos
      does not have (matched by organisasjonsnummer, then name), with the old
      program's number in ``party_id`` when it is free. Create them with
      ``POST …/parties``.

    Then upload the file again: the lines come back on the new accounts, and 1500
    and 2400 split per customer and supplier. Send the lines on to ``/kontroll``
    or ``POST /inngaende-balanse``, edited if need be.

    Findings, besides the review's own (see ``/kontroll``):
    ``kontoer_mangler`` and ``parter_mangler`` (``feil``: add them first);
    ``udisponert_resultat`` (the balances did not sum to zero because the old
    program had not posted last year's result to equity; it is placed on 2050);
    ``mva_flyttet_til_2740``; ``kontoer_slatt_sammen`` (several of the file's
    accounts on one four-digit account); ``gruppering_avviker`` and
    ``gruppering_fra_filen_brukes_ikke`` (the file's næringsspesifikasjon
    grouping differs from Myntos's, for the accountant to look at);
    ``reskontro_avviker`` (the file's customer or supplier balances do not add
    up to the account); ``bankkonto_utenfor_1920`` (the company's bank account
    is on another account in the file, a driftskreditt on 2390, say, and that
    account is not marked as a bank account, so the overview would not count
    the money on it: mark it with ``PATCH …/kontoplan/{account_id}`` and
    ``is_bank_account: true``, or move the line to 1920 if the accountant
    prefers).

    The file is not kept. Keep it yourself: it is the documentation of the
    books before Myntos. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if file.size is not None and file.size > reader.MAX_UPLOAD_BYTES:
        raise InvalidSaftFileError(
            "filen er større enn 200 MB, som er det største Altinn tar imot av en SAF-T-fil"
        )
    found = await run_in_threadpool(reader.read, file.file)
    result = await opening_import.from_balances(db, company=context.company, found=found)
    response = _review(context.company.id, result.review)
    return SaftOpeningResponse(
        **response.model_dump(),
        source=SaftSourceResponse(
            software=result.source.software,
            audit_file_version=result.source.audit_file_version,
            orgnr=result.source.orgnr,
            company_name=result.source.company_name,
            start_date=result.source.start,
            end_date=result.source.end,
            balances=result.source.balances,
        ),
        accounts_to_add=[
            AccountToAddResponse(
                number=add.number,
                description=add.description,
                account_type=add.account_type,
                grouping_category=add.grouping_category,
                grouping_code=add.grouping_code,
                source_accounts=list(add.source_accounts),
            )
            for add in result.accounts_to_add
        ],
        parties_to_add=[
            PartyToAddResponse(
                party_type=add.party_type,
                name=add.name,
                orgnr=add.orgnr,
                party_id=add.party_id,
                address_line=add.address_line,
                postal_code=add.postal_code,
                city=add.city,
                source_id=add.source_id,
            )
            for add in result.parties_to_add
        ],
    )


@router.post(
    "/companies/{company_id}/inngaende-balanse",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    openapi_extra=key_post("føre inngående balanse"),
)
async def post_opening(
    payload: PostOpeningRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> PostedVoucherResponse | JSONResponse:
    """Post the opening balance (inngående balanse). Cannot be undone, only reversed.

    Takes the same ``lines`` as ``POST /inngaende-balanse/kontroll`` and runs
    the same check on them again, so the button the page enabled and the rule
    enforced here cannot disagree. Call the check first and show its answer:
    what is posted is a voucher in the ``IB`` journal dated the first day of the
    company's first year in Myntos, and under bokføringsloven a posted voucher
    is never edited or deleted. A mistake is corrected by a reversal plus a
    replacement.

    ``korriger``: false for the first opening balance; true to replace the one
    standing, which reverses it and posts these lines as its linked
    replacement in the same request, so there is never a moment with two
    opening balances or with none. Without it a second post is refused rather
    than doubling every balance.

    Answers 201 with the voucher. Refusals:

    - 422 ``invalid_opening_balance``: a ``feil`` finding; ``findings`` lists
      them all, with the codes the check documents.
    - 409 ``opening_balance_exists``: one is posted already. Send
      ``korriger: true`` if the person means to replace it.
    - 409 ``no_opening_balance``: ``korriger`` with nothing to replace.
    - 409 ``opening_balance_not_allowed``: no financial year yet, or the first
      year is closed and has to be reopened first.

    Needs ``ledger:write`` (a member or above). For an API key it needs the
    ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    try:
        transaction = await opening.post_opening(
            db,
            company=context.company,
            lines=[line.to_domain() for line in payload.lines],
            correct=payload.korriger,
            created_by_user_id=user.id,
        )
    except InvalidOpeningBalanceError as error:
        return _refused(str(error), "invalid_opening_balance", error.findings)
    return _posted(context.company.id, transaction)


@router.post(
    "/companies/{company_id}/inngaende-balanse/reversering",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("reversere inngående balanse"),
)
async def reverse_opening(
    context: CurrentCompany, db: DbSession, user: CurrentUser
) -> PostedVoucherResponse:
    """Withdraw the standing opening balance by posting its reversal. No body.

    The opening voucher stays in the books beside a reversal that cancels it,
    because a posted voucher is never deleted (bokføringsloven). Afterwards the
    company has no opening balance, and a new one may be posted with
    ``korriger`` false. To replace it with corrected figures, call
    ``POST /inngaende-balanse`` with ``korriger: true`` instead, which reverses
    and replaces in one step.

    The ordinary reversal of a voucher refuses the opening voucher (409
    ``cannot_reverse_here``): this is its route, so the opening balance has one
    way in and one way out. Answers 201 with the reversal. Refusals: 409
    ``no_opening_balance`` (nothing standing to withdraw) and 409
    ``opening_balance_not_allowed`` (the first year is closed; reopen it first).

    Needs ``ledger:write`` (a member or above). For an API key it needs the
    ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    reversal = await opening.reverse_opening(
        db, company=context.company, created_by_user_id=user.id
    )
    return _posted(context.company.id, reversal)


# --- Regnskapsår og årsavslutning ----------------------------------------------------


class YearEventResponse(BaseModel):
    action: str
    occurred_at: datetime
    user_id: int | None
    reason: str | None
    result: Decimal | None
    tax: Decimal | None


class YearResponse(BaseModel):
    company_id: int
    year: int
    start_date: date
    end_date: date
    closed: bool
    closed_at: datetime | None
    events: list[YearEventResponse]


@router.get("/companies/{company_id}/regnskapsaar", openapi_extra=KEY_READ)
async def list_years(context: CurrentCompany, db: DbSession) -> list[YearResponse]:
    """The company's financial years (regnskapsår), open and closed, oldest first.

    Each year has its dates, whether it is closed (avsluttet), and the log of
    every close and reopening with who did it and, for a reopening, why. A
    closed year takes no new entries: anything dated in it is refused until it
    is reopened. Use this to pick the year for the year-end close, the SAF-T
    export or the opening balance. Any role that can read the books may call it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return [
        YearResponse(
            company_id=context.company.id,
            year=item.year,
            start_date=item.start_date,
            end_date=item.end_date,
            closed=item.closed_at is not None,
            closed_at=item.closed_at,
            events=[
                YearEventResponse(
                    action=e.action,
                    occurred_at=e.occurred_at,
                    user_id=e.user_id,
                    reason=e.reason,
                    result=e.result,
                    tax=e.tax,
                )
                for e in item.events
            ],
        )
        for item in await closing.list_years(db, company=context.company)
    ]


class PlannedLineResponse(BaseModel):
    account_number: str
    description: str
    debit: Decimal
    credit: Decimal


class ClosingPlanResponse(BaseModel):
    company_id: int
    year: int
    organisation_form: str | None
    closed: bool
    closed_at: datetime | None
    revenue: Decimal
    costs: Decimal
    result_before_tax: Decimal
    tax: Decimal
    result: Decimal
    tax_lines: list[PlannedLineResponse]
    disposition_lines: list[PlannedLineResponse]
    closable: bool
    findings: list[FindingResponse]


def _plan(company_id: int, plan: closing.ClosingPlan) -> ClosingPlanResponse:
    def lines(items: list[closing.PlannedLine]) -> list[PlannedLineResponse]:
        return [
            PlannedLineResponse(
                account_number=i.account_number,
                description=i.description,
                debit=i.debit,
                credit=i.credit,
            )
            for i in items
        ]

    return ClosingPlanResponse(
        company_id=company_id,
        year=plan.year,
        organisation_form=plan.organisation_form,
        closed=plan.closed_at is not None,
        closed_at=plan.closed_at,
        revenue=plan.revenue,
        costs=plan.costs,
        result_before_tax=plan.result_before_tax,
        tax=plan.tax,
        result=plan.result,
        tax_lines=lines(plan.tax_lines),
        disposition_lines=lines(plan.disposition_lines),
        closable=plan.closable,
        findings=_findings(plan.findings),
    )


_Year = Annotated[int, Query(ge=2000, le=2100)]


@router.get("/companies/{company_id}/arsavslutning", openapi_extra=KEY_READ)
async def closing_plan(
    aar: _Year,
    context: CurrentCompany,
    db: DbSession,
    skattekostnad: Annotated[Decimal, Query()] = Decimal("0.00"),
) -> ClosingPlanResponse:
    """What closing financial year ``aar`` would do (årsavslutning), without doing it.

    Closing a year moves its result (income minus costs) onto equity, so the
    next year opens its income and cost accounts at zero, and then locks the
    year so nothing more can be posted in it. This is the review step: the
    year's ``revenue``, ``costs``, ``result_before_tax``, ``tax`` and
    ``result``, the vouchers the close would post (``tax_lines``,
    ``disposition_lines``) and ``findings``. Show it to the person before
    ``POST /arsavslutning``, with the same ``skattekostnad``.

    How the result is placed depends on the organisation form, read off the
    company's registration in Enhetsregisteret rather than asked:

    - AS (aksjeselskap, limited company): first the tax cost the person states
      in ``skattekostnad`` (8300 against 2500; Myntos does not calculate tax).
      A profit then covers any uncovered loss from earlier years (udekket tap,
      8990/2080) and the rest goes to other equity (8960/2050). A loss is
      covered by other equity as far as it goes and the rest is carried as
      udekket tap, rather than driving 2050 negative.
    - ENK (enkeltpersonforetak, sole proprietorship): no tax, since the owner
      is the taxpayer. The result and the year's private drawings on 2060 go
      straight to equity (8800/2050).
    - Any other form is refused by name: their rules for dividing a result
      differ, and an AS disposition would be a confident wrong answer.

    ``closable`` is false while a ``feil`` finding stands: ``allerede_avsluttet``,
    ``aaret_er_ikke_over`` (a year closes after its last day, Oslo time),
    ``tidligere_aar_aapent`` (years close in order, because a year's opening
    equity comes from the one before), ``ukjent_organisasjonsform``,
    ``organisasjonsform_ikke_stottet``, ``ugyldig_skatt``, ``skatt_for_enk`` and
    ``allerede_disponert`` (the closing accounts already carry postings).
    Notes (``merknad``) to show: ``ingen_skattekostnad``, ``privatkonto_i_as``,
    ``mva_ikke_gjort_opp`` (a VAT period of the year is not settled, and cannot
    be once the year is closed), ``ingen_inngaende_balanse``, and
    ``avskrivning_ikke_fort`` / ``avskrivning_utdatert`` (the anleggsregister's
    depreciation for the year is not posted, or is posted and out of date;
    ``/anlegg/avskrivning``).

    404 ``financial_year_not_found`` for a year the company does not keep in
    Myntos. Needs read access only.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    plan = await closing.plan_closing(
        db, company=context.company, year=aar, today=_today(), tax=skattekostnad
    )
    return _plan(context.company.id, plan)


class CloseYearRequest(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    skattekostnad: Decimal = Decimal("0.00")
    # The review step's confirmation. A close posts vouchers and locks the year
    # for every colleague; a request that did not come through the review is
    # refused rather than taken as consent.
    bekreftet: bool = False


class CloseYearResponse(ClosingPlanResponse):
    vouchers: list[PostedVoucherResponse]


@router.post(
    "/companies/{company_id}/arsavslutning",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    openapi_extra=key_post("avslutte et regnskapsår"),
)
async def close_year(
    payload: CloseYearRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CloseYearResponse | JSONResponse:
    """Close a financial year (årsavslutning): post the result onto equity and lock the year.

    Body: ``aar``; ``skattekostnad``, an AS's tax cost for the year as a
    decimal string (``"0.00"`` for none, and always for an ENK); and
    ``bekreftet``, the person's yes to the review. Call
    ``GET /arsavslutning?aar=&skattekostnad=`` with the same figures first and
    show it. Without ``bekreftet: true`` the request is refused (422
    ``confirmation_required``) rather than taken as consent, because a close
    posts vouchers and locks the year for every colleague.

    The plan is worked out again here, so what is posted is what the rules say
    at this moment, not what a page showed a minute ago. Up to two vouchers in
    the ``AVS`` journal, dated the year's last day (the tax cost, then the
    disposition), then the lock: nothing can be posted with a date in the year,
    by anyone in the company, and its account groupings in the kontoplan are
    fixed, until the year is reopened. The close is logged with who did it and
    the figures, and the next year is opened. Closing does not file anything:
    the annual accounts and the tax return are separate.

    Answers 201 with the plan as posted and ``vouchers``. Refusals: 422
    ``year_not_closable`` with ``findings`` (the blocking codes
    ``GET /arsavslutning`` documents) and 404 ``financial_year_not_found``.

    Needs ``ledger:close`` (an administrator or owner). For an API key it
    needs the ``post`` scope, and answers 202 ``approval_pending`` when the
    person approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_CLOSE)
    if not payload.bekreftet:
        return _refused(
            "årsavslutningen må bekreftes etter at den er gjennomgått",
            "confirmation_required",
            [],
        )
    try:
        plan, posted = await closing.close_year(
            db,
            company=context.company,
            year=payload.aar,
            today=_today(),
            tax=payload.skattekostnad,
            created_by_user_id=user.id,
        )
    except YearNotClosableError as error:
        return _refused(str(error), "year_not_closable", error.findings)
    summary = _plan(context.company.id, plan).model_dump()
    summary["closed"] = True
    return CloseYearResponse(**summary, vouchers=[_posted(context.company.id, t) for t in posted])


class ReopenYearRequest(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    begrunnelse: str = Field(default="", max_length=1000)
    # That the årsregnskap for the year has not been fastsatt. Once it has, an
    # error is corrected in the open year instead.
    bekreftet: bool = False


class ReopenYearResponse(BaseModel):
    company_id: int
    year: int
    reversals: list[PostedVoucherResponse]


@router.post(
    "/companies/{company_id}/arsavslutning/gjenapning",
    openapi_extra=key_post("åpne et avsluttet regnskapsår igjen"),
)
async def reopen_year(
    payload: ReopenYearRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> ReopenYearResponse:
    """Reopen a closed financial year (gjenåpning), with a reason.

    For the ordinary case: a supplier invoice for last year turns up after the
    year was closed, before the annual accounts (årsregnskap) were adopted.
    Nothing is deleted. The closing vouchers are reversed and stay in the books
    beside their reversals, the lock is cleared, and the reopening is logged
    with the person's name and reason, so an auditor asking whether the year
    changed after it was closed gets an answer. Close it again afterwards.

    Body: ``aar``; ``begrunnelse``, the person's own reason, at least 10
    characters and at most 1000, kept in the log and shown to everyone with
    access; and ``bekreftet``, the person's confirmation that the årsregnskap
    for the year has **not** been adopted (fastsatt). Once it has, an error is
    corrected in the current year against equity, not by changing the year
    that was reported. Myntos cannot see the adoption, so this rests on the
    person's word; an agent must ask them, never assume.

    Only the latest closed year can be reopened, because a later closed year's
    opening rests on this one's closing. Answers 200 with ``reversals``.
    Refusals: 409 ``year_not_reopenable`` (the year is open, or a later year is
    closed and must be reopened first), 422 ``reopening_reason_required``
    (reason too short, or ``bekreftet`` not true) and 404
    ``financial_year_not_found``.

    Needs ``ledger:close`` (an administrator or owner). For an API key it
    needs the ``post`` scope, and answers 202 ``approval_pending`` when the
    person approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_CLOSE)
    reversals = await closing.reopen_year(
        db,
        company=context.company,
        year=payload.aar,
        reason=payload.begrunnelse,
        confirmed=payload.bekreftet,
        created_by_user_id=user.id,
    )
    return ReopenYearResponse(
        company_id=context.company.id,
        year=payload.aar,
        reversals=[_posted(context.company.id, t) for t in reversals],
    )


# --- Oppløsning ------------------------------------------------------------------------


class WindUpRequest(BaseModel):
    # The last day of the books: the day the last thing happened in the
    # company, usually the payout to the shareholders.
    siste_dag: date
    # That the company has been dissolved (oppløst). Ending the books changes
    # what may be posted for every colleague and what the skattemelding is, so
    # a request that did not come through the page's question is refused.
    bekreftet: bool = False


class WindUpResponse(BaseModel):
    company_id: int
    # The last day of the books, or null while the company is in business.
    last_day: date | None
    # The financial year that ends on it.
    year: int | None
    year_closed: bool


def _wound_up(company_id: int, state: winding_up.WindingUp) -> WindUpResponse:
    return WindUpResponse(
        company_id=company_id,
        last_day=state.last_day,
        year=state.year,
        year_closed=state.year_closed,
    )


@router.post(
    "/companies/{company_id}/arsavslutning/opplosning",
    response_model=None,
    openapi_extra=key_post("avslutte regnskapet fordi selskapet er oppløst"),
)
async def wind_up(
    payload: WindUpRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> WindUpResponse | JSONResponse:
    """End the books on the company's last day, because it has been dissolved (oppløsning).

    For an aksjeselskap that has been wound up (oppløst, aksjeloven kapittel
    16): its debts paid, what was left paid out to the shareholders, and the
    company deleted or about to be deleted from Enhetsregisteret. Its last
    financial year then ends on ``siste_dag`` instead of 31 December, so it
    can be closed the day after (``POST /arsavslutning``), and that year's
    skattemelding is filed early, as a forhåndsfastsetting
    (``GET /skattemelding`` says ``forhaandsfastsetting: true``), which
    skatteforvaltningsloven § 8-2 asks for before the company is gone.

    Body: ``siste_dag``, the last day of the books as an ISO date, which is the
    person's to say: the day the last thing happened in the company, usually
    the payout to the shareholders, not necessarily the day Brønnøysund
    deleted it; and ``bekreftet``, the person's yes that the company has been
    dissolved. Without ``bekreftet: true`` it is refused (422
    ``confirmation_required``).

    From then on nothing can be posted after ``siste_dag`` (422
    ``period_outside_books`` on any posting), closing the year opens no next
    one, and ``last_day`` is on the company (``wound_up_on`` in
    ``GET /companies/{company_id}``). Logged in the year's events
    (``GET /regnskapsaar``, action ``opplost``). Undone with
    ``POST /arsavslutning/opplosning/angre`` while the year is open.

    Refusals: 409 ``winding_up_conflict`` (the books already end on another
    day, the year is closed, a later year exists, or entries are dated after
    the day; the message says which and how many); 422
    ``invalid_winding_up_date`` (a day in the future, or the year's first
    day); 404 ``financial_year_not_found`` for a year the company does not
    keep in Myntos.

    Needs ``ledger:close`` (an administrator or owner). For an API key it needs
    the ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_CLOSE)
    if not payload.bekreftet:
        return _refused(
            "bekreft at selskapet er oppløst før regnskapet avsluttes",
            "confirmation_required",
            [],
        )
    state = await winding_up.end_books(
        db,
        company=context.company,
        last_day=payload.siste_dag,
        today=_today(),
        created_by_user_id=user.id,
    )
    return _wound_up(context.company.id, state)


@router.post(
    "/companies/{company_id}/arsavslutning/opplosning/angre",
    openapi_extra=key_post("angre at regnskapet er avsluttet ved oppløsning"),
)
async def undo_wind_up(context: CurrentCompany, db: DbSession, user: CurrentUser) -> WindUpResponse:
    """Undo ending the books at oppløsning: the last year runs to 31 December again.

    For a day chosen by mistake, or a company that was not dissolved after
    all. Allowed while the last year is open; the change is logged in the
    year's events (action ``opplost_angret``). To choose another day, undo and
    end the books again.

    Refusals: 409 ``winding_up_conflict`` (the books do not end early, the year
    is closed and must be reopened first, or the year's skattemelding has been
    sent to Skatteetaten as a forhåndsfastsetting, which rests on the day).

    Needs ``ledger:close`` (an administrator or owner). For an API key it needs
    the ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_CLOSE)
    state = await winding_up.undo(db, company=context.company, created_by_user_id=user.id)
    return _wound_up(context.company.id, state)
