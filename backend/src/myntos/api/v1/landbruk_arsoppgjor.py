"""The farm's year-end lists: the count of livestock and stock, and private uttak.

Both are review-then-post, like the opening balance: the page asks what would
be posted and what is wrong with it, shows that, and only then offers the
button. The check runs again on the post.

``GET  /companies/{id}/landbruk/telling?aar=``
    The year, the takseringsregler's categories, last year's closing rows, the
    balances that need a line, the count standing and its history.
``POST /companies/{id}/landbruk/telling/kontroll``
    The count priced and compared with the books. Posts nothing.
``POST /companies/{id}/landbruk/telling``
    Post it; with ``korriger`` replace the standing one.
``POST /companies/{id}/landbruk/telling/reversering``
    Withdraw the standing count.
``…/landbruk/uttak``, ``…/uttak/kontroll``, ``…/uttak``, ``…/uttak/reversering``
    The same four for the list of private uttak.

Only the add-on's companies: the domain refuses the rest with 409
``segment_not_enabled``. The year is a query or body field, looked up through
the company, so no second id appears in a path. Rules in
:mod:`myntos.domain.arsoppgjor.buskap` and :mod:`.uttak`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status
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
from myntos.domain.arsoppgjor import buskap, uttak
from myntos.domain.arsoppgjor import takseringsregler as regler
from myntos.domain.arsoppgjor.errors import InvalidCountError, InvalidUttakError
from myntos.domain.arsoppgjor.findings import Finding
from myntos.domain.identity.access import Permission, require_permission

router = APIRouter(tags=["landbruk"])

Year = Annotated[int, Query(ge=2000, le=2100, description="The financial year («regnskapsår»).")]


class FindingResponse(BaseModel):
    severity: str
    code: str
    message: str


def _findings(findings: list[Finding]) -> list[FindingResponse]:
    return [
        FindingResponse(severity=f.severity.value, code=f.code, message=f.message) for f in findings
    ]


def _refused(detail: str, code: str, findings: list[Finding]) -> JSONResponse:
    # Answered here rather than raised, as the opening balance does: what the
    # person needs is the whole list, not the first item of it.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": detail,
            "code": code,
            "findings": [f.model_dump() for f in _findings(findings)],
        },
    )


# --- The catalogue ------------------------------------------------------------------


class CategoryResponse(BaseModel):
    code: str
    kind: str
    label: str
    unit: str
    #: The year's value per unit, or null when the rule is not a figure.
    value: Decimal | None
    valuation: str
    species: str | None
    adult: bool
    nibio: str | None


class ProductResponse(BaseModel):
    code: str
    label: str
    unit: str
    value: Decimal | None
    account: str
    food: bool
    note: str


class RatesResponse(BaseModel):
    #: The year the categories are from. Equal to the year asked for when its
    #: rates are published; otherwise the latest published year's categories,
    #: and ``published`` is false.
    year: int
    published: bool
    source: str
    url: str


def _rates(year: int, catalogue: regler.YearRates) -> RatesResponse:
    return RatesResponse(
        year=catalogue.year,
        published=regler.for_year(year) is not None,
        source=catalogue.source,
        url=catalogue.url,
    )


def _category(c: regler.CountCategory) -> CategoryResponse:
    return CategoryResponse(
        code=c.code,
        kind=c.kind.value,
        label=c.label,
        unit=c.unit,
        value=c.value,
        valuation=c.valuation.value,
        species=c.species,
        adult=c.adult,
        nibio=c.nibio,
    )


def _product(p: regler.UttakProduct) -> ProductResponse:
    return ProductResponse(
        code=p.code,
        label=p.label,
        unit=p.unit,
        value=p.value,
        account=p.account,
        food=p.food,
        note=p.note,
    )


# --- Telling ------------------------------------------------------------------------


class CountLineRequest(BaseModel):
    #: ``inngaende`` (1 January) or ``utgaende`` (31 December).
    side: str = Field(min_length=1, max_length=16)
    category: str = Field(min_length=1, max_length=64)
    quantity: Decimal
    description: str = Field(default="", max_length=255)
    kind: str | None = Field(default=None, max_length=16)
    unit: str | None = Field(default=None, max_length=16)
    valuation: str | None = Field(default=None, max_length=16)
    unit_value: Decimal | None = None
    account_number: str | None = Field(default=None, max_length=32)

    def to_domain(self) -> buskap.CountLineInput:
        return buskap.CountLineInput(
            side=self.side.strip(),
            category=self.category.strip(),
            quantity=self.quantity,
            description=self.description,
            kind=self.kind,
            unit=self.unit,
            valuation=self.valuation,
            unit_value=self.unit_value,
            account_number=self.account_number,
        )


class CountRequest(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    lines: list[CountLineRequest] = Field(max_length=500)


class PostCountRequest(CountRequest):
    # Replacing the standing count is something the person has to mean; without
    # it a second count is refused rather than moving 1450 twice.
    korriger: bool = False


class YearRequest(BaseModel):
    aar: int = Field(ge=2000, le=2100)


class CountLineResponse(BaseModel):
    side: str
    kind: str
    category: str
    label: str
    description: str
    quantity: Decimal
    unit: str
    valuation: str
    unit_value: Decimal
    value: Decimal
    account_number: str
    nibio: str | None


def _count_line(line: buskap.CountLine) -> CountLineResponse:
    return CountLineResponse(
        side=line.side.value,
        kind=line.kind.value,
        category=line.category,
        label=line.label,
        description=line.description,
        quantity=line.quantity,
        unit=line.unit,
        valuation=line.valuation,
        unit_value=line.unit_value,
        value=line.value,
        account_number=line.account_number,
        nibio=line.nibio,
    )


class KindTotalResponse(BaseModel):
    kind: str
    #: The element in the næringsspesifikasjon's
    #: ``spesifikasjonAvSkattemessigVarelager`` this total is.
    naeringsspesifikasjon: str
    opening: Decimal
    closing: Decimal


def _totals(totals: list[buskap.KindTotal]) -> list[KindTotalResponse]:
    return [
        KindTotalResponse(
            kind=t.kind.value,
            naeringsspesifikasjon=t.naeringsspesifikasjon,
            opening=t.opening,
            closing=t.closing,
        )
        for t in totals
    ]


class AccountChangeResponse(BaseModel):
    account_number: str
    account_description: str | None
    kind: str
    booked: Decimal
    counted: Decimal
    change: Decimal
    opening_booked: Decimal
    opening_counted: Decimal


class VoucherLineResponse(BaseModel):
    account_number: str
    description: str
    debit: Decimal
    credit: Decimal


class CountReviewResponse(BaseModel):
    company_id: int
    year: int
    counted_on: date | None
    opening_source: str
    opening: list[CountLineResponse]
    closing: list[CountLineResponse]
    accounts: list[AccountChangeResponse]
    voucher_lines: list[VoucherLineResponse]
    totals: list[KindTotalResponse]
    postable: bool
    findings: list[FindingResponse]


def _count_review(company_id: int, checked: buskap.CountReview) -> CountReviewResponse:
    return CountReviewResponse(
        company_id=company_id,
        year=checked.year,
        counted_on=checked.counted_on,
        opening_source=checked.opening_source.value,
        opening=[_count_line(li) for li in checked.opening],
        closing=[_count_line(li) for li in checked.closing],
        accounts=[
            AccountChangeResponse(
                account_number=a.account_number,
                account_description=a.account_description,
                kind=a.kind.value,
                booked=a.booked,
                counted=a.counted,
                change=a.change,
                opening_booked=a.opening_booked,
                opening_counted=a.opening_counted,
            )
            for a in checked.accounts
        ],
        voucher_lines=[
            VoucherLineResponse(
                account_number=v.account_number,
                description=v.description,
                debit=v.debit,
                credit=v.credit,
            )
            for v in checked.voucher_lines
        ],
        totals=_totals(checked.totals),
        postable=checked.postable,
        findings=_findings(checked.findings),
    )


class CountResponse(BaseModel):
    company_id: int
    id: int
    year: int
    counted_on: date
    opening_source: str
    withdrawn: bool
    corrects_id: int | None
    voucher_id: int | None
    voucher_no: str | None
    created_at: datetime
    opening: list[CountLineResponse]
    closing: list[CountLineResponse]
    totals: list[KindTotalResponse]


def _count(company_id: int, view: buskap.CountView) -> CountResponse:
    return CountResponse(
        company_id=company_id,
        id=view.id,
        year=view.year,
        counted_on=view.counted_on,
        opening_source=view.opening_source,
        withdrawn=view.withdrawn,
        corrects_id=view.corrects_id,
        voucher_id=view.voucher_id,
        voucher_no=view.voucher_no,
        created_at=view.created_at,
        opening=[_count_line(li) for li in view.opening],
        closing=[_count_line(li) for li in view.closing],
        totals=_totals(view.totals),
    )


class BookedAccountResponse(BaseModel):
    account_number: str
    account_description: str
    kind: str
    balance: Decimal


class CountStateResponse(BaseModel):
    company_id: int
    year: int
    counted_on: date | None
    allowed: bool
    refusal: str | None
    rates: RatesResponse
    categories: list[CategoryResponse]
    opening_source: str
    opening: list[CountLineResponse]
    booked: list[BookedAccountResponse]
    standing: CountResponse | None
    history: list[CountResponse]


@router.get("/companies/{company_id}/landbruk/telling", openapi_extra=KEY_READ)
async def count_state(aar: Year, context: CurrentCompany, db: DbSession) -> CountStateResponse:
    """The farm's year-end count of livestock and stock («telling av buskap og varelager»).

    A farm's herd (1450), the hay, silage and grain grown for its own animals
    (1490) and the feed, fertiliser and seed it bought and has not used (1400)
    are counted on 31 December and valued by Skattedirektoratet's yearly
    takseringsregler. This says what the page needs before anybody types:

    - ``rates``: which year's regulation the ``categories`` come from, and
      ``published`` false when Skattedirektoratet has not published the year
      asked for (December). Then lines valued at ``normalverdi`` are refused;
      the average and kostpris need no rates.
    - ``categories``: what may be counted, with each one's ``kind`` (``buskap``,
      ``egenprodusert``, ``innkjopt``), unit, value and default ``valuation``.
      ``annet`` is accepted too, for anything the table does not name.
    - ``opening`` and ``opening_source``: the opening rows. ``forrige_telling``
      means last year's closing rows, copied unchanged (send no opening rows);
      ``oppgitt`` means the first year in Myntos, where the person types them
      from the previous program's list.
    - ``booked``: accounts in the inventory ranges (1400-1459, 1490) holding a
      balance on 31 December. Each needs a closing line, or the count is refused.
    - ``standing`` and ``history``: the count that stands, and every list ever
      posted for the year, withdrawals included.

    Only for companies with the farming add-on (409 ``segment_not_enabled``).
    Needs ``ledger:read``; every key may read it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await buskap.state(db, company=context.company, year=aar)
    return CountStateResponse(
        company_id=context.company.id,
        year=found.year,
        counted_on=found.counted_on,
        allowed=found.refusal is None,
        refusal=found.refusal,
        rates=_rates(aar, found.catalogue),
        categories=[_category(c) for c in found.catalogue.categories],
        opening_source=found.opening_source.value,
        opening=[_count_line(li) for li in found.opening],
        booked=[
            BookedAccountResponse(
                account_number=b.account_number,
                account_description=b.account_description,
                kind=b.kind.value,
                balance=b.balance,
            )
            for b in found.booked
        ],
        standing=_count(context.company.id, found.standing) if found.standing else None,
        history=[_count(context.company.id, v) for v in found.history],
    )


@router.post("/companies/{company_id}/landbruk/telling/kontroll", openapi_extra=KEY_PREVIEW)
async def review_count(
    payload: CountRequest, context: CurrentCompany, db: DbSession
) -> CountReviewResponse:
    """Price a count and show the voucher it would post, without posting or storing anything.

    Each line: ``side`` (``utgaende`` for 31 December; ``inngaende`` only in
    the first year in Myntos), ``category`` (a code from
    ``GET …/landbruk/telling``, or ``annet`` with ``kind``), ``quantity``
    (heads, kilos or FEm, as a decimal string), and when the category's rule
    is not a figure, ``unit_value`` (a decimal string). ``valuation`` overrides
    the category's own: ``normalverdi`` (the year's published value),
    ``snitt`` (adults from the opening balance: the average of the adult
    animals of the species in the opening rows, computed here, rounded to 10
    kroner), ``kostpris`` (what it cost), ``verdsatt`` (a stated value, reason
    in ``description``). ``account_number`` defaults to 1450, 1400 or 1490 by
    kind; NIBIO's 1451-1458 per species are accepted for livestock.

    The answer: each line priced, per account the books' balance on 31
    December, the counted value and the change, the voucher lines (the
    balance account against 4690, 4090 or 4290), the totals per kind under the
    næringsspesifikasjon's names, and ``findings``. A ``feil`` stops posting; a
    ``merknad`` is for the person to read. Codes include ``ingen_satser``
    (the year's rates are not published), ``konto_uten_telling`` (an account
    holding a balance has no line: count it, 0 if it is gone),
    ``inngaende_fra_forrige_telling`` (last year's count is the opening; send
    no opening rows), ``ingen_voksne_inngaende`` (the average needs adult
    animals in the opening rows), ``normalverdi_fast``, ``mangler_verdi``,
    ``mangler_begrunnelse``, ``aar_ikke_aapent``.

    Needs ``ledger:read``. Stores nothing, so a key that may only ask to write
    runs it at once.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    checked = await buskap.review(
        db,
        company=context.company,
        year=payload.aar,
        lines=[line.to_domain() for line in payload.lines],
    )
    return _count_review(context.company.id, checked)


@router.post(
    "/companies/{company_id}/landbruk/telling",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    openapi_extra=key_post("føre tellingen av buskap og varelager"),
)
async def post_count(
    payload: PostCountRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CountResponse | JSONResponse:
    """Post the year's count. Cannot be undone, only replaced or withdrawn.

    Takes the same body as ``POST …/landbruk/telling/kontroll`` and runs the
    same check again. Call the check first and show the person its answer:
    what is posted is one voucher in the ``TELLING`` journal dated 31 December,
    and under bokføringsloven a posted voucher is never edited. A count that
    changes no value is stored without a voucher.

    ``korriger``: false for the year's first count; true to replace the one
    standing, which reverses its voucher and posts these lines in the same
    request.

    Answers 201 with the stored count. Refusals:

    - 422 ``invalid_count``: a ``feil`` finding; ``findings`` lists them all.
    - 409 ``count_exists``: a count stands. Send ``korriger: true`` if the
      person means to replace it.
    - 409 ``no_count``: ``korriger`` with nothing to replace.
    - 409 ``segment_not_enabled``: the company does not have the farming add-on.

    Needs ``ledger:write`` (a member or above). For an API key it needs the
    ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    try:
        stored = await buskap.post_count(
            db,
            company=context.company,
            year=payload.aar,
            lines=[line.to_domain() for line in payload.lines],
            correct=payload.korriger,
            created_by_user_id=user.id,
        )
    except InvalidCountError as error:
        return _refused(str(error), "invalid_count", error.findings)
    view = await buskap.year_count(db, company=context.company, year=payload.aar)
    assert view is not None and view.id == stored.id  # noqa: S101 - just stored
    return _count(context.company.id, view)


@router.post(
    "/companies/{company_id}/landbruk/telling/reversering",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("trekke tilbake tellingen av buskap og varelager"),
)
async def withdraw_count(
    payload: YearRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CountResponse:
    """Withdraw the year's standing count: its voucher is reversed, nothing replaces it.

    The voucher stays in the books beside its reversal, and the list stays in
    the history; an empty list is stored after it to say that no count stands.
    To replace the count with corrected figures, post with ``korriger: true``
    instead. The register's ordinary reversal refuses the count's voucher (409
    ``cannot_reverse_here``): this is its route.

    Answers 201 with the empty list stored. Refusals: 409 ``no_count``
    (nothing stands), 409 ``farm_year_not_open`` (the year is closed or not in
    Myntos; reopen it first). Needs ``ledger:write``; for a key, the ``post``
    scope, and it may answer 202 ``approval_pending``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    stored = await buskap.withdraw_count(
        db, company=context.company, year=payload.aar, created_by_user_id=user.id
    )
    found = await buskap.state(db, company=context.company, year=payload.aar)
    view = next(v for v in found.history if v.id == stored.id)
    return _count(context.company.id, view)


# --- Private uttak --------------------------------------------------------------------


class UttakLineRequest(BaseModel):
    product: str = Field(min_length=1, max_length=64)
    quantity: Decimal
    description: str = Field(default="", max_length=255)
    unit_value: Decimal | None = None
    unit: str | None = Field(default=None, max_length=16)
    account_number: str | None = Field(default=None, max_length=32)

    def to_domain(self) -> uttak.UttakLineInput:
        return uttak.UttakLineInput(
            product=self.product.strip(),
            quantity=self.quantity,
            description=self.description,
            unit_value=self.unit_value,
            unit=self.unit,
            account_number=self.account_number,
        )


class UttakRequest(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    #: The voucher date; the last day of the year when left out.
    dato: date | None = None
    lines: list[UttakLineRequest] = Field(max_length=200)


class PostUttakRequest(UttakRequest):
    korriger: bool = False


class UttakLineResponse(BaseModel):
    product: str
    label: str
    description: str
    quantity: Decimal
    unit: str
    unit_value: Decimal
    value: Decimal
    account_number: str
    tax_code: str | None
    vat_rate: Decimal | None
    vat: Decimal
    #: ``fritatt`` (primærnæring on the yearly melding, mval. § 6-20),
    #: ``avgiftspliktig`` (taxed as a sale) or ``uregistrert``.
    vat_reason: str


def _uttak_line(line: uttak.UttakLine) -> UttakLineResponse:
    return UttakLineResponse(
        product=line.product,
        label=line.label,
        description=line.description,
        quantity=line.quantity,
        unit=line.unit,
        unit_value=line.unit_value,
        value=line.value,
        account_number=line.account_number,
        tax_code=line.tax_code,
        vat_rate=line.vat_rate,
        vat=line.vat,
        vat_reason=line.vat_reason,
    )


class UttakReviewResponse(BaseModel):
    company_id: int
    year: int
    occurred_on: date | None
    lines: list[UttakLineResponse]
    total_value: Decimal
    total_vat: Decimal
    postable: bool
    findings: list[FindingResponse]


class UttakResponse(BaseModel):
    company_id: int
    id: int
    year: int
    occurred_on: date
    withdrawn: bool
    corrects_id: int | None
    voucher_id: int | None
    voucher_no: str | None
    created_at: datetime
    lines: list[UttakLineResponse]
    total_value: Decimal
    total_vat: Decimal


def _uttak(company_id: int, view: uttak.UttakView) -> UttakResponse:
    return UttakResponse(
        company_id=company_id,
        id=view.id,
        year=view.year,
        occurred_on=view.occurred_on,
        withdrawn=view.withdrawn,
        corrects_id=view.corrects_id,
        voucher_id=view.voucher_id,
        voucher_no=view.voucher_no,
        created_at=view.created_at,
        lines=[_uttak_line(li) for li in view.lines],
        total_value=view.total_value,
        total_vat=view.total_vat,
    )


class UttakStateResponse(BaseModel):
    company_id: int
    year: int
    occurred_on: date | None
    allowed: bool
    refusal: str | None
    rates: RatesResponse
    products: list[ProductResponse]
    standing: UttakResponse | None
    history: list[UttakResponse]


@router.get("/companies/{company_id}/landbruk/uttak", openapi_extra=KEY_READ)
async def uttak_state(aar: Year, context: CurrentCompany, db: DbSession) -> UttakStateResponse:
    """The farm's private uttak for a year («private uttak av egne produkter»).

    What the household took from the farm (milk, potatoes, meat, eggs,
    firewood, stall and hay for a horse) is income to the farm and a
    withdrawal by the owner of an ENK, listed as bokføringsforskriften § 3-1
    nr. 5 asks. ``products`` are what may be listed, each with its norm
    (``value``: milk 410 and potatoes 130 kr per household member a year,
    firewood 750 kr per loose m³ …) or null when it is valued at what it would
    have fetched (``omsetningsverdi``), and its uttak account (3161-3166).
    ``annet`` is accepted too. ``rates.published`` false means
    Skattedirektoratet has not published the year's norms yet (December).

    ``standing`` is the list that stands, ``history`` every list posted for the
    year, withdrawals (no lines) included.

    Only for companies with the farming add-on (409 ``segment_not_enabled``).
    Needs ``ledger:read``; every key may read it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await uttak.state(db, company=context.company, year=aar)
    return UttakStateResponse(
        company_id=context.company.id,
        year=found.year,
        occurred_on=found.occurred_on,
        allowed=found.refusal is None,
        refusal=found.refusal,
        rates=_rates(aar, found.catalogue),
        products=[_product(p) for p in (*found.catalogue.products, regler.ANNET_PRODUCT)],
        standing=_uttak(context.company.id, found.standing) if found.standing else None,
        history=[_uttak(context.company.id, v) for v in found.history],
    )


@router.post("/companies/{company_id}/landbruk/uttak/kontroll", openapi_extra=KEY_PREVIEW)
async def review_uttak(
    payload: UttakRequest, context: CurrentCompany, db: DbSession
) -> UttakReviewResponse:
    """Value a list of private uttak and decide its VAT, without posting or storing anything.

    Each line: ``product`` (a code from ``GET …/landbruk/uttak``), ``quantity``
    (household members for milk and potatoes, 0.5 for those the norm halves;
    loose m³ for firewood; horses; kilos), and ``unit_value`` for a product
    with no norm (what it would have fetched). Send amounts as decimal strings.
    A norm may be departed from (a foal's stall) with the reason in
    ``description``. ``dato`` is the voucher date, 31 December by default: it
    decides which VAT term the uttak is reported in.

    The VAT per line, in ``vat_reason``: ``fritatt`` (code 5) when the line
    falls in a primærnæring melding filed once a year (merverdiavgiftsloven
    § 6-20, FMVA § 6-20-1); ``avgiftspliktig`` otherwise for a registered
    company, 15 % (code 31) for food and 25 % (code 3) for the rest;
    ``uregistrert`` without a code when the company was not VAT-registered.

    ``findings``: a ``feil`` stops posting, a ``merknad`` is to be read. Codes
    include ``ingen_satser``, ``mangler_verdi``, ``mangler_begrunnelse``,
    ``ikke_for_aksjeselskap`` (an AS has no privatkonto: its owner's uttak is
    salary, a loan or a dividend), ``dato_utenfor_aaret``, ``aar_ikke_aapent``,
    ``norm_som_mva_grunnlag``.

    Needs ``ledger:read``. Stores nothing, so a key that may only ask to write
    runs it at once.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    checked = await uttak.review(
        db,
        company=context.company,
        year=payload.aar,
        lines=[line.to_domain() for line in payload.lines],
        occurred_on=payload.dato,
    )
    return UttakReviewResponse(
        company_id=context.company.id,
        year=checked.year,
        occurred_on=checked.occurred_on,
        lines=[_uttak_line(li) for li in checked.lines],
        total_value=checked.total_value,
        total_vat=checked.total_vat,
        postable=checked.postable,
        findings=_findings(checked.findings),
    )


@router.post(
    "/companies/{company_id}/landbruk/uttak",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    openapi_extra=key_post("føre private uttak"),
)
async def post_uttak(
    payload: PostUttakRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> UttakResponse | JSONResponse:
    """Post the year's list of private uttak. Cannot be undone, only replaced or withdrawn.

    Takes the same body as ``POST …/landbruk/uttak/kontroll`` and runs the same
    check again; show the person that answer first. Posts one voucher in the
    ``UTTAK`` journal: 2060 Privatkonto debited with value and VAT, each
    product's uttak account credited, and 2700 with the VAT when there is any.

    ``korriger``: true to replace the standing list, which reverses its voucher
    and posts these lines in the same request.

    Answers 201 with the stored list. Refusals: 422 ``invalid_uttak`` (with
    ``findings``), 409 ``uttak_exists`` (send ``korriger: true`` if the person
    means to replace it), 409 ``no_uttak`` (``korriger`` with nothing to
    replace), 409 ``segment_not_enabled``.

    Needs ``ledger:write`` (a member or above). For an API key it needs the
    ``post`` scope, and answers 202 ``approval_pending`` when the person
    approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    try:
        stored = await uttak.post_uttak(
            db,
            company=context.company,
            year=payload.aar,
            lines=[line.to_domain() for line in payload.lines],
            occurred_on=payload.dato,
            correct=payload.korriger,
            created_by_user_id=user.id,
        )
    except InvalidUttakError as error:
        return _refused(str(error), "invalid_uttak", error.findings)
    found = await uttak.state(db, company=context.company, year=payload.aar)
    view = next(v for v in found.history if v.id == stored.id)
    return _uttak(context.company.id, view)


@router.post(
    "/companies/{company_id}/landbruk/uttak/reversering",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("trekke tilbake listen over private uttak"),
)
async def withdraw_uttak(
    payload: YearRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> UttakResponse:
    """Withdraw the year's standing list of private uttak: its voucher is reversed.

    The voucher stays beside its reversal, and the list in the history, with
    an empty list stored after it. To replace the list with corrected figures,
    post with ``korriger: true`` instead. Answers 201 with the empty list.
    Refusals: 409 ``no_uttak``, 409 ``farm_year_not_open``. Needs
    ``ledger:write``; for a key, the ``post`` scope, and it may answer 202
    ``approval_pending``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    stored = await uttak.withdraw_uttak(
        db, company=context.company, year=payload.aar, created_by_user_id=user.id
    )
    found = await uttak.state(db, company=context.company, year=payload.aar)
    view = next(v for v in found.history if v.id == stored.id)
    return _uttak(context.company.id, view)
