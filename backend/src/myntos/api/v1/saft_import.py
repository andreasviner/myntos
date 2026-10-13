"""The previous program's SAF-T file: its vouchers brought over, and its books compared.

The opening balance is read from the same file on its own route
(``POST …/inngaende-balanse/saft``, in :mod:`myntos.api.v1.arsoppgjor`). These
are the two things a company needs after that when it does not move on
1 January, or moves slowly:

``POST /companies/{id}/saft/bilag/kontroll``
    What bringing the year's vouchers over would post, and what stops it.
    Stores nothing.

``POST /companies/{id}/saft/bilag``
    Posts them, all or nothing, into the ``IMPORT`` journal.

``POST /companies/{id}/saft/sammenligning``
    The old program's latest file against Myntos's books, for a company
    running both side by side. Stores nothing, and needs only to read.

The file is uploaded with each call and never kept; the person keeps it, as the
documentation of the books before Myntos. Every route reads it whole in a
worker thread (:func:`myntos.saft.reader.read_entries`), because a year of
vouchers is seconds of parsing that must not hold the event loop.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    key_post,
)
from myntos.api.v1.arsoppgjor import (
    AccountToAddResponse,
    FindingResponse,
    PartyToAddResponse,
    _findings,
    _refused,
)
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger.models import PartyType
from myntos.saft import compare as comparison
from myntos.saft import reader, voucher_import
from myntos.saft.errors import InvalidSaftFileError, InvalidVoucherImportError
from myntos.saft.opening_import import AccountToAdd, PartyToAdd
from myntos.saft.reader import SaftEntries

router = APIRouter(tags=["saft"])

_File = Annotated[UploadFile, File()]
_Til = Annotated[date | None, Form()]


class ImportSourceResponse(BaseModel):
    software: str | None
    audit_file_version: str
    orgnr: str
    company_name: str
    start_date: date
    end_date: date


class MonthResponse(BaseModel):
    month: int
    vouchers: int
    total: Decimal


class DimensionToAddResponse(BaseModel):
    """An avdeling, prosjekt or produkt the file's lines carry that the books lack."""

    #: The dimension's code: the company's own, or the one to make it with.
    code: str
    dimension_description: str
    #: The company's dimension to add the value to; null when the dimension
    #: itself has to be made first (``POST …/dimensjoner``).
    dimension_id: int | None
    value: str
    description: str


class VoucherImportResponse(BaseModel):
    company_id: int
    source: ImportSourceResponse
    year: int
    through: date
    vouchers: int
    lines: int
    first_date: date | None
    last_date: date | None
    already_imported: int
    later: int
    months: list[MonthResponse]
    accounts_to_add: list[AccountToAddResponse]
    parties_to_add: list[PartyToAddResponse]
    dimensions_to_add: list[DimensionToAddResponse] = []
    findings: list[FindingResponse]
    postable: bool


class ImportedVouchersResponse(BaseModel):
    company_id: int
    posted: int
    journal: str
    first_voucher_no: str
    last_voucher_no: str
    first_date: date
    last_date: date


class AccountDifferenceResponse(BaseModel):
    account_number: str
    description: str
    in_file: Decimal
    in_myntos: Decimal
    difference: Decimal
    months: list[int]


class PartyDifferenceResponse(BaseModel):
    party_type: PartyType
    name: str
    source_id: str | None
    party_id: int | None
    in_file: Decimal
    in_myntos: Decimal
    difference: Decimal


class VatDifferenceResponse(BaseModel):
    termin: str
    code: str
    spesifikasjon: str | None
    base_in_file: Decimal
    base_in_myntos: Decimal
    vat_in_file: Decimal
    vat_in_myntos: Decimal


class ComparisonResponse(BaseModel):
    company_id: int
    source: ImportSourceResponse
    start_date: date
    through: date
    matches: bool
    accounts_compared: int
    parties_compared: int
    terms_compared: int
    accounts: list[AccountDifferenceResponse]
    parties: list[PartyDifferenceResponse]
    vat: list[VatDifferenceResponse]
    findings: list[FindingResponse]


async def _read(file: UploadFile) -> SaftEntries:
    if file.size is not None and file.size > reader.MAX_UPLOAD_BYTES:
        raise InvalidSaftFileError(
            "filen er større enn 200 MB, som er det største Altinn tar imot av en SAF-T-fil"
        )
    return await run_in_threadpool(reader.read_entries, file.file)


def _source(source: voucher_import.ImportSource) -> ImportSourceResponse:
    return ImportSourceResponse(
        software=source.software,
        audit_file_version=source.audit_file_version,
        orgnr=source.orgnr,
        company_name=source.company_name,
        start_date=source.start,
        end_date=source.end,
    )


def _account(add: AccountToAdd) -> AccountToAddResponse:
    return AccountToAddResponse(
        number=add.number,
        description=add.description,
        account_type=add.account_type,
        grouping_category=add.grouping_category,
        grouping_code=add.grouping_code,
        source_accounts=list(add.source_accounts),
    )


def _party(add: PartyToAdd) -> PartyToAddResponse:
    return PartyToAddResponse(
        party_type=add.party_type,
        name=add.name,
        orgnr=add.orgnr,
        party_id=add.party_id,
        address_line=add.address_line,
        postal_code=add.postal_code,
        city=add.city,
        source_id=add.source_id,
    )


@router.post("/companies/{company_id}/saft/bilag/kontroll", openapi_extra=KEY_PREVIEW)
async def review_voucher_import(
    context: CurrentCompany, db: DbSession, file: _File, til: _Til = None
) -> VoucherImportResponse:
    """What bringing the year's vouchers over from the old program would post. Stores nothing.

    For a company that moves to Myntos in the middle of a year, or keeps its
    old program a while and runs Myntos beside it: the old program's SAF-T file
    holds every voucher of the year so far, and this reads them into vouchers
    for Myntos's ``IMPORT`` journal. Upload the file as ``multipart/form-data``
    in ``file`` (the XML, or the ZIP it downloads as, at most 200 MB and in one
    part), with ``til`` (YYYY-MM-DD, optional) as the last day to bring over;
    without it, everything in the file.

    The file must be this company's (else 422 ``saft_wrong_company``) and for
    its first year in Myntos, from 1 January (else 422 ``saft_wrong_year``),
    and the opening balance for that year must be posted first, from the same
    file (``POST …/inngaende-balanse/saft``).

    Each voucher keeps the old program's number (``2026-240``). Tripletex adds
    a payment to the invoice's voucher on another date; such a voucher becomes
    one voucher per date (``2026-1-0109``). Lines land on the first four digits
    of the file's account id, or where the opening balance moved that account,
    with the old id in the line's text, and keep their VAT code, rate, basis and
    amounts as the old program reported them. Vouchers an earlier import brought
    over are skipped, so a later file brings only what is new.

    A line's avdeling, prosjekt or driftsgren comes along when the file has
    one (``Analysis`` of a dimension the file names «Avdeling», «Prosjekt»,
    «Driftsgren» or «Produkt»); the file's other dimensions, such as
    Tripletex's «Bilagsart» and «Ansatte», stay behind. With the farming
    add-on on, an account id with NIBIO's department after the four digits
    (``291610``, ``145120512``) gives the line that «Avdeling», and the three
    digits after it a «Produkt», unless the line names its own avdeling.

    The answer counts what would be posted (``vouchers``, ``lines``, per
    month in ``months``), what was skipped (``already_imported``, ``later``),
    and, as the opening balance's SAF-T read does, ``accounts_to_add`` and
    ``parties_to_add``: create them with ``POST …/kontoplan`` and
    ``POST …/parties`` and read the file again. ``dimensions_to_add`` lists
    the values the lines carry that the company's dimensions lack: make the
    dimension with ``POST …/dimensjoner`` (``code``, ``dimension_description``)
    when ``dimension_id`` is null, then each value with ``POST
    …/dimensjoner/{dimension_id}/verdier`` (``value``, ``description``). They
    have to exist first, because a posted line cannot be given one afterwards.
    ``postable`` is true when
    ``POST …/saft/bilag`` would post.

    Findings (``feil`` stops the post): ``inngaende_balanse_mangler``,
    ``aaret_er_avsluttet``, ``bilag_i_perioden`` (Myntos already has vouchers
    in the period that did not come from the file, so the same trade would be
    booked twice: choose an earlier ``til``), ``kontoer_mangler``,
    ``parter_mangler``, ``dimensjoner_mangler``, ``bilag_i_ubalanse``, ``ukjent_konto``,
    ``mva_kode_ukjent``, ``flere_mva_koder``, ``ingen_periode``; and as
    ``merknad``: ``konto_som_inngaende_balanse``, ``bilag_delt_paa_dato``,
    ``allerede_hentet``, ``bilag_etter_perioden``, ``bilag_for_aaret``,
    ``mva_kode_utelatt``, ``gruppering_fra_filen_brukes_ikke``,
    ``dimensjoner_utelatt``, ``avdeling_fra_kontonummer``,
    ``avdeling_ulik_kontonummer``, ``dimensjon_delt``, ``ingen_bilag``
    and ``mva_meldinger_sendt`` (the VAT returns the old program filed must not
    be filed again from Myntos, which does not know they were). Needs
    ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    found = await _read(file)
    planned = await voucher_import.plan(db, company=context.company, found=found, til=til)
    return VoucherImportResponse(
        company_id=context.company.id,
        source=_source(planned.source),
        year=planned.year,
        through=planned.through,
        vouchers=len(planned.vouchers),
        lines=planned.line_count,
        first_date=planned.first_date,
        last_date=planned.last_date,
        already_imported=planned.already_imported,
        later=planned.later,
        months=[
            MonthResponse(month=m.month, vouchers=m.vouchers, total=m.total) for m in planned.months
        ],
        accounts_to_add=[_account(add) for add in planned.accounts_to_add],
        parties_to_add=[_party(add) for add in planned.parties_to_add],
        dimensions_to_add=[
            DimensionToAddResponse(
                code=add.code,
                dimension_description=add.dimension_description,
                dimension_id=add.dimension_id,
                value=add.value,
                description=add.description,
            )
            for add in planned.dimensions_to_add
        ],
        findings=_findings(planned.findings),
        postable=planned.postable,
    )


@router.post(
    "/companies/{company_id}/saft/bilag",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    openapi_extra=key_post("føre bilagene fra det gamle regnskapsprogrammet"),
)
async def post_voucher_import(
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    file: _File,
    til: _Til = None,
) -> ImportedVouchersResponse | JSONResponse:
    """Post the year's vouchers from the old program's SAF-T file. Cannot be undone.

    Takes the same ``file`` and ``til`` as ``POST …/saft/bilag/kontroll`` and
    runs the same review again, so what the page showed and what is posted
    cannot disagree; call that first and show its answer. Everything it
    proposes is posted in one go, or nothing is: 422
    ``invalid_voucher_import`` with ``findings`` when any finding is a
    ``feil`` or there is nothing new to post.

    The vouchers go into the ``IMPORT`` journal under the old program's
    numbers, posted under bokføringsloven like any other: never edited or
    deleted. They are not shown in the register (``…/cashflow``), which lists
    what was registered in Myntos, and the register will not reverse them; a
    mistake in one is corrected in the old program and comes across with the
    next import. They count everywhere else: balances, the MVA-melding per
    term, the year-end and the SAF-T export.

    Needs ``ledger:write``. For an API key it needs the ``post`` scope, and
    answers 202 ``approval_pending`` when the person approves the key's posts
    one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    found = await _read(file)
    try:
        posted = await voucher_import.post(
            db, company=context.company, found=found, til=til, created_by_user_id=user.id
        )
    except InvalidVoucherImportError as error:
        return _refused(str(error), "invalid_voucher_import", error.findings)
    return ImportedVouchersResponse(
        company_id=context.company.id,
        posted=posted.posted,
        journal="IMPORT",
        first_voucher_no=posted.first_voucher_no,
        last_voucher_no=posted.last_voucher_no,
        first_date=posted.first_date,
        last_date=posted.last_date,
    )


@router.post("/companies/{company_id}/saft/sammenligning", openapi_extra=KEY_READ)
async def compare_with_file(
    context: CurrentCompany, db: DbSession, file: _File, til: _Til = None
) -> ComparisonResponse:
    """The old program's books against Myntos's, from its SAF-T file. Stores nothing.

    For a company running Myntos beside the program it keeps its books in (a
    parallel run): export a fresh SAF-T file there and upload it here as
    ``multipart/form-data`` in ``file``, with ``til`` (YYYY-MM-DD, optional)
    to stop earlier than the file's last voucher. The file must be this
    company's (422 ``saft_wrong_company``) and start on 1 January (422
    ``saft_wrong_year``).

    Compared from 1 January to ``through`` (the last voucher in the file, or
    ``til``), the opening balance left out of both sides:

    - ``accounts``: each account whose movement differs, with both figures and
      the ``months`` in which they part. The file's accounts land as the import
      puts them. The VAT accounts are one row (2740), because each program
      settles a term into 2740 only when it files the return itself.
    - ``parties``: customers and suppliers whose movement on 1500 and 2400
      differs; ``party_id`` is null for one Myntos does not have.
    - ``vat``: per VAT term and code (and spesifikasjon), the basis and the VAT
      that differ, signed debit-positive as the ledger has them.

    ``matches`` is true when nothing differs. Amounts are decimal strings;
    ``difference`` is Myntos less the file. Needs ``ledger:read``: an
    accountant with read access is who will run it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await _read(file)
    result = await comparison.compare(db, company=context.company, found=found, til=til)
    return ComparisonResponse(
        company_id=context.company.id,
        source=_source(result.source),
        start_date=result.start,
        through=result.through,
        matches=result.matches,
        accounts_compared=result.accounts_compared,
        parties_compared=result.parties_compared,
        terms_compared=result.terms_compared,
        accounts=[
            AccountDifferenceResponse(
                account_number=a.account_number,
                description=a.description,
                in_file=a.in_file,
                in_myntos=a.in_myntos,
                difference=a.difference,
                months=list(a.months),
            )
            for a in result.accounts
        ],
        parties=[
            PartyDifferenceResponse(
                party_type=p.party_type,
                name=p.name,
                source_id=p.source_id,
                party_id=p.party_pk,
                in_file=p.in_file,
                in_myntos=p.in_myntos,
                difference=p.difference,
            )
            for p in result.parties
        ],
        vat=[
            VatDifferenceResponse(
                termin=v.termin,
                code=v.code,
                spesifikasjon=v.spesifikasjon,
                base_in_file=v.base_in_file,
                base_in_myntos=v.base_in_myntos,
                vat_in_file=v.vat_in_file,
                vat_in_myntos=v.vat_in_myntos,
            )
            for v in result.vat
        ],
        findings=_findings(result.findings),
    )
