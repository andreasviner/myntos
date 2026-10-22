"""What a waiting request acts on, said by Myntos from the books.

A request a key asked for is shown to its person before they approve it, with
the route's own label («reversere en betaling») and whatever the key sent in
the body. For a route that acts on a record named in its path, that was not
enough: ``POST …/cashflow/{voucher_id}/reversal`` has no body, so the card said
which kind of thing would happen and never to what, and the only sentence
naming the voucher was the agent's own note. An agent that read a hostile
invoice (docs/agents-and-api.md rule 7) could ask to reverse one voucher and
say it was another, and the person had nothing of Myntos's to check it against.

So each record in the path is looked up here, through its company like every
sub-resource, and described in one line from what the books say about it. The
agent writes none of it. A record that is not there any more says so, because
the request will be refused for the same reason when it runs.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.anlegg.register import get_asset
from myntos.domain.bank.service import get_line
from myntos.domain.faktura.errors import FakturaError
from myntos.domain.faktura.models import DocumentType, InvoiceStatus
from myntos.domain.faktura.service import get_document
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.dimensions import get_dimension, get_value
from myntos.domain.ledger.errors import LedgerError
from myntos.domain.ledger.kontoplan import get_account
from myntos.domain.ledger.models import Company, PartyType
from myntos.domain.ledger.parties import get_party
from myntos.domain.ledger.service import get_voucher
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.codes import Meldingskategori
from myntos.domain.mva.periods import PeriodType
from myntos.saft.jobs import ImportNotFoundError
from myntos.saft.models import ImportPurpose, SaftImport
from myntos.skattemelding import service as skattemelding_service

Describe = Callable[[AsyncSession, Company, str, int], Awaitable[str]]


def _dato(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _kroner(value: Decimal) -> str:
    """``1250`` → ``1 250,00``."""
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",")


async def _voucher(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    voucher = await get_voucher(db, company=company, voucher_pk=pk)
    amount = sum((line.debit for line in voucher.lines), Decimal("0.00"))
    return (
        f"Bilag {voucher.transaction_id}, {_dato(voucher.transaction_date)}: "
        f"{voucher.description}, {_kroner(amount)} kr"
    )


async def _document(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    document = await get_document(db, company=company, document_pk=pk)
    kind = "kreditnota" if document.document_type == DocumentType.KREDITNOTA else "faktura"
    if document.status == InvoiceStatus.UTKAST or document.document_number is None:
        return f"Utkast til {kind} til {document.buyer_name}"
    issued = f", utstedt {_dato(document.issue_date)}" if document.issue_date else ""
    return f"{kind.capitalize()} nr. {document.document_number} til {document.buyer_name}{issued}"


async def _filing(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    # One parameter name for two tables: the path says which.
    if "/skattemelding/" in path:
        melding = await skattemelding_service.get_filing(db, company, pk)
        return f"Skattemeldingen for {melding.year}"
    filing = await mva_service.get_filing(db, company, pk)
    period = (
        f"{filing.year}"
        if filing.period_type == PeriodType.AAR.value
        else f"termin {filing.period_index} i {filing.year}"
    )
    category = " (primærnæring)" if filing.category == Meldingskategori.PRIMAERNAERING else ""
    return f"MVA-meldingen for {period}{category}"


async def _asset(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    asset = await get_asset(db, company=company, asset_id=pk)
    return f"Driftsmidlet «{asset.description}», anskaffet {_dato(asset.acquired_on)}"


async def _account(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    account = await get_account(db, company=company, account_pk=pk)
    return f"Konto {account.account_number} {account.description}"


async def _party(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    party = await get_party(db, company=company, party_pk=pk)
    kind = "Kunden" if party.party_type == PartyType.KUNDE else "Leverandøren"
    return f"{kind} {party.name}"


async def _bank_line(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    line = await get_line(db, company=company, line_pk=pk)
    text = line.counterparty_name or line.remittance or "uten tekst"
    way = "inn" if line.direction == CashFlowDirection.INN else "ut"
    return f"Banklinjen {_dato(line.booking_date)}: {text}, {_kroner(line.amount)} kr {way}"


async def _dimension(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    dimension = await get_dimension(db, company=company, dimension_pk=pk)
    return f"Dimensjonen «{dimension.description}»"


async def _dimension_value(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    value = await get_value(db, company=company, value_pk=pk)
    return f"«{value.description}» under {value.dimension_type.description}"


async def _import(db: AsyncSession, company: Company, path: str, pk: int) -> str:
    # Two kinds share the id: the SAF-T files, which keep the plain name, and
    # the old program's printout, whose card says what goes into the archive
    # for good. Counted from the stored review, never the uploaded file's name,
    # which the key chose.
    if "/saft/vedlegg/" not in path:
        return f"SAF-T-innlesingen med nummer {pk}"
    job = await db.scalar(
        select(SaftImport).where(
            SaftImport.id == pk,
            SaftImport.company_id == company.id,
            SaftImport.purpose == ImportPurpose.VEDLEGG.value,
        )
    )
    if job is None:
        raise ImportNotFoundError("finner ikke opplastingen")
    review = job.review or {}
    return (
        f"Utskriften av bilag fra Tripletex (opplasting {pk}): "
        f"{review.get('documents', 0)} dokumenter fra {review.get('to_attach', 0)} bilag "
        "legges i bilagsarkivet"
    )


_DESCRIBE: dict[str, Describe] = {
    "voucher_id": _voucher,
    "document_id": _document,
    "filing_id": _filing,
    "asset_id": _asset,
    "account_id": _account,
    "party_pk": _party,
    "line_id": _bank_line,
    "dimension_id": _dimension,
    "dimension_value_id": _dimension_value,
    "import_id": _import,
}

# Records with no lookup of their own above, named so the person at least
# sees which one. Plain, because a guess at a description would be worse than
# none.
_NAMED: dict[str, str] = {
    "adjustment_id": "Mva-justeringen",
    "reading_id": "Lesingen av bilaget",
}

_GONE: dict[str, str] = {
    "voucher_id": "Bilaget",
    "document_id": "Fakturaen",
    "filing_id": "Meldingen",
    "asset_id": "Driftsmidlet",
    "account_id": "Kontoen",
    "party_pk": "Kunden eller leverandøren",
    "line_id": "Banklinjen",
    "dimension_id": "Dimensjonen",
    "dimension_value_id": "Verdien",
    "import_id": "Opplastingen",
}


def path_parameters(app: Any, method: str, path: str) -> dict[str, str]:
    """The named parts of ``path``, read off the route it goes to.

    Matched against the published document in the order
    :func:`myntos.auth.approvals.operation_for` uses, so the template found
    here is the one the request was held for.
    """
    openapi = getattr(app, "openapi", None)
    if openapi is None:
        return {}
    paths: dict[str, dict[str, Any]] = openapi()["paths"]
    for template in sorted(paths, key=lambda each: each.count("{")):
        if method.lower() not in paths[template]:
            continue
        names = re.findall(r"\{([^/{}]+)\}", template)
        literal = re.split(r"\{[^/{}]+\}", template)
        match = re.fullmatch("([^/]+)".join(re.escape(part) for part in literal), path)
        if match is not None:
            return dict(zip(names, match.groups(), strict=True))
    return {}


async def describe(
    db: AsyncSession, app: Any, company: Company, method: str, path: str
) -> list[str]:
    """One line for each record the request's path names, the company aside."""
    lines: list[str] = []
    for name, raw in path_parameters(app, method, path).items():
        if name == "company_id" or not raw.isdigit():
            continue
        pk = int(raw)
        describe_one = _DESCRIBE.get(name)
        if describe_one is None:
            lines.append(f"{_NAMED.get(name, name)} med nummer {pk}")
            continue
        try:
            lines.append(await describe_one(db, company, path, pk))
        except (LedgerError, FakturaError):
            lines.append(f"{_GONE[name]} finnes ikke i regnskapet")
    return lines
