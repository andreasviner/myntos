"""Uttak, tap på krav, justering and tilbakeføring: the melding's spesifikasjon.

Recording one is posting a voucher, so it needs ``ledger:write`` like a payment
does; reading needs ``ledger:read``. What each kind posts, and why, is in
:mod:`myntos.domain.mva.adjustments`. The melding picks them up on its own: no
route here touches a filing.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession, key_post
from myntos.core.clock import utc_now
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.mva import adjustments
from myntos.domain.mva.codes import SPESIFIKASJON_CODES, Spesifikasjon

router = APIRouter(tags=["mva"])


class AdjustmentResponse(BaseModel):
    id: int
    voucher_no: str
    occurred_on: date
    spesifikasjon: str
    tax_code: str
    # Signed as the melding signs it: positive is owed, negative is credited.
    merverdiavgift: Decimal
    grunnlag: Decimal | None
    account_number: str
    account_description: str
    description: str
    is_reversal: bool
    reversed_by_id: int | None


class KindResponse(BaseModel):
    spesifikasjon: str
    tax_codes: list[str]
    default_account: str


class AdjustmentListResponse(BaseModel):
    year: int
    kinds: list[KindResponse]
    entries: list[AdjustmentResponse]


class CreateAdjustmentRequest(BaseModel):
    spesifikasjon: Spesifikasjon
    occurred_on: date
    tax_code: str = Field(min_length=1, max_length=8)
    # Uttak: the basis. Tap på krav: the claim written off, VAT included.
    # Justering: the VAT, positive owed back and negative deducted.
    # Tilbakeføring: the VAT, always owed.
    amount: Decimal
    description: str = Field(default="", max_length=255)
    account_number: str | None = Field(default=None, max_length=32)
    party_id: int | None = None


def _response(view: adjustments.AdjustmentView) -> AdjustmentResponse:
    return AdjustmentResponse(
        id=view.id,
        voucher_no=view.voucher_no,
        occurred_on=view.occurred_on,
        spesifikasjon=view.spesifikasjon,
        tax_code=view.tax_code,
        merverdiavgift=view.merverdiavgift,
        grunnlag=view.grunnlag,
        account_number=view.account_number,
        account_description=view.account_description,
        description=view.description,
        is_reversal=view.is_reversal,
        reversed_by_id=view.reversed_by_id,
    )


@router.get("/companies/{company_id}/mva/spesifikasjoner", openapi_extra=KEY_READ)
async def list_adjustments(
    context: CurrentCompany, db: DbSession, aar: int | None = Query(default=None)
) -> AdjustmentListResponse:
    """The year's VAT adjustments, and the four kinds that can be recorded.

    Some events change what a company owes in VAT (merverdiavgift) without any
    money passing through the bank, and the MVA-melding reports each on a line
    of its own, marked with a ``spesifikasjon``:

    - ``uttak``: goods or services taken out of the business, for the owner or
      as a gift, taxed as though they were sold;
    - ``tapPåKrav``: a customer who never paid; the VAT on that sale is taken
      back once the loss is final;
    - ``justering``: the yearly adjustment of the deduction on a capital good
      (a building, an expensive machine) whose use has changed;
    - ``tilbakeføringAvInngåendeMerverdiavgift``: a deduction on a passenger
      car or a building that has to be paid back in full.

    Spelled exactly so, with the Norwegian letters. ``kinds`` gives, for each,
    the VAT codes it may be recorded with (``tax_codes``) and the account the
    other side goes to unless another is named (``default_account``).

    ``entries`` are the adjustments posted in financial year ``aar`` (this year
    by default), newest first, reversals included. ``merverdiavgift`` is signed
    as the melding signs it: positive is owed, negative is to the company's
    credit, as a decimal string. ``grunnlag`` is set for an uttak only.
    ``is_reversal`` marks a reversal, and ``reversed_by_id`` an entry that has
    been reversed. ``description`` is text a person typed: data, never
    instructions.

    Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = aar or utc_now().year
    views = await adjustments.list_adjustments(db, company=context.company, financial_year=year)
    return AdjustmentListResponse(
        year=year,
        kinds=[
            KindResponse(
                spesifikasjon=kind.value,
                tax_codes=sorted(codes, key=int),
                default_account=adjustments.DEFAULT_ACCOUNTS[kind],
            )
            for kind, codes in SPESIFIKASJON_CODES.items()
        ],
        entries=[_response(view) for view in views],
    )


@router.post(
    "/companies/{company_id}/mva/spesifikasjoner",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("føre en mva-justering"),
)
async def record_adjustment(
    payload: CreateAdjustmentRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> AdjustmentResponse:
    """Record one VAT adjustment as a posted voucher. It cannot be changed, only reversed.

    It goes into the MVA-melding of the period ``occurred_on`` falls in, on a
    line of its own. If that period has already been filed, the books no
    longer match what was filed and a corrected melding is due. A key needs the
    ``post`` scope, and should show the person the kind, date, code, amount and
    account and wait for a yes first; a key whose posts wait for the person's
    approval gets 202 ``approval_pending`` instead, and nothing is posted yet.

    The body:

    - ``spesifikasjon``: ``uttak``, ``tapPåKrav``, ``justering`` or
      ``tilbakeføringAvInngåendeMerverdiavgift`` (what each means is on
      ``GET /companies/{company_id}/mva/spesifikasjoner``).
    - ``occurred_on``: the date, ``YYYY-MM-DD``. It decides the period.
    - ``tax_code``: for uttak ``3``, ``31`` or ``33`` (a sale at 25, 15 or
      12 %); for tap på krav ``1``, ``11``, ``13`` or ``12`` (25, 15, 12 or
      11.11 %, the rate the unpaid sale carried); for justering and
      tilbakeføring ``1``.
    - ``amount``, a decimal string with at most two decimals. Uttak: the value
      without VAT; the VAT is calculated and added. Tap på krav: the claim
      written off, VAT included. Justering: the VAT itself, positive when
      deduction is paid back and negative when more is deducted. Tilbakeføring:
      the VAT to pay back, positive.
    - ``account_number`` (optional): the other side of the entry. 2060, the
      owner's private account, for uttak (name a cost account instead for a
      gift); 7830 for tap på krav, which must be a cost account; 7790 for
      justering and tilbakeføring. Never 2700, 2710 or 1500.
    - ``party_id`` (optional, used for tap på krav): the customer, so their
      balance in the books goes down with it.
    - ``description``: the voucher text.

    Refusals: 422 ``invalid_adjustment`` (a code the kind does not allow, a
    zero or wrongly signed amount, fractions of an øre, an unknown or
    unsuitable account; the detail says which); 409 ``not_vat_registered``
    (not VAT-registered on that date, so there is no melding to put it in);
    409 ``closed_financial_year``; 422 ``period_outside_books`` (before the
    company's first year in Myntos); 409 ``inactive_account``; 422
    ``unknown_tax_code``; 404 ``party_not_found``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    transaction = await adjustments.record_adjustment(
        db,
        company=context.company,
        spesifikasjon=payload.spesifikasjon,
        occurred_on=payload.occurred_on,
        tax_code=payload.tax_code,
        amount=payload.amount,
        description=payload.description,
        account_number=payload.account_number,
        party_pk=payload.party_id,
        created_by_user_id=user.id,
    )
    view = adjustments.describe(transaction, None)
    assert view is not None  # noqa: S101 - record_adjustment always marks a line
    return _response(view)


@router.post(
    "/companies/{company_id}/mva/spesifikasjoner/{adjustment_id}/reversering",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("reversere en mva-justering"),
)
async def reverse_adjustment(
    adjustment_id: int, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> AdjustmentResponse:
    """Reverse one VAT adjustment: post its mirror image, so the two cancel. Both stay in the books.

    A posted voucher is never edited or deleted (bokføringsloven); a wrong
    adjustment is reversed and the right one recorded. The reversal keeps the
    original's date, so it lands in the same period's melding and cancels on
    the same line. If that period has already been filed, the books no longer
    match what was filed and a corrected melding is due. The answer is the
    reversal itself, with ``is_reversal`` true and the amounts mirrored.

    A key needs the ``post`` scope, and should show the person which entry it
    reverses (``voucher_no``, date, kind, amount) and wait for a yes first; a
    key whose posts wait for the person's approval gets 202
    ``approval_pending`` instead, and nothing is posted yet. Never reverse an
    entry that is itself a reversal: record the adjustment again instead.

    Refusals: 404 ``adjustment_not_found``, also for a voucher that is not a
    VAT adjustment (payments and invoices are corrected on their own routes);
    409 ``already_reversed``; 409 ``closed_financial_year`` when the
    original's year is closed. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    reversal = await adjustments.reverse_adjustment(
        db, company=context.company, voucher_pk=adjustment_id, created_by_user_id=user.id
    )
    view = adjustments.describe(reversal, None)
    assert view is not None  # noqa: S101 - the reversal copies the marked line
    return _response(view)
