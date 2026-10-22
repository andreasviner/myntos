"""The skattemelding from the books: load the figures, build the pair, keep what was checked.

The one module in :mod:`myntos.skattemelding` that reads the database. It turns
a company's year into the plain :class:`~myntos.skattemelding.naeringsspesifikasjon.Grunnlag`
the builder takes, from three places that already answer the question for
someone else:

- **the accounts**, opening and closing, with the post each is grouped on, from
  :func:`myntos.saft.service.load`, which already decides what a year's opening
  balance is and what an ``IB`` voucher counts as. A second reading of the
  ledger here would be a second answer to that question, and the two would
  drift;
- **the saldo specification**, from the anleggsregister
  (:func:`myntos.domain.anlegg.saldo.specification`);
- **whether the company has a revisor**, from the Enhetsregisteret snapshot the
  company page keeps (``REVI`` among the roles). Not a guess when there is no
  snapshot: the review says to fetch it, because the answer decides the Altinn
  instance's revisor step, and both ways of getting it wrong leave a filing
  Skatteetaten has to sort out by hand (docs/integrations/skatteetaten.md).
  Nor for a company the register has **deleted**, whose roles it no longer
  lists at all: the person says, at the check.

A **wound-up company's last year** (``company.wound_up_on``) ends on its last
day, not on 31 December, and is filed as soon as that day is past, as a
forhåndsfastsetting (myntos.domain.arsoppgjor.winding_up).

Like :mod:`myntos.domain.mva.service`, every query goes through the company:
filings are looked up with their ``company_id``, never by id alone
(docs/company-isolation.md).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos import __version__
from myntos.core.clock import utc_now
from myntos.domain.anlegg import saldo as saldo_module
from myntos.domain.arsoppgjor.errors import FinancialYearNotFoundError
from myntos.domain.arsoppgjor.findings import Finding, Severity, feil, merknad
from myntos.domain.identity.models import User
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company, FinancialYear
from myntos.saft import service as saft_service
from myntos.skattemelding import codes, schema, upersonlig, xmlwriter
from myntos.skattemelding.envelope import FetchedMelding, Validering
from myntos.skattemelding.errors import (
    SkattemeldingFilingNotFoundError,
    SkattemeldingYearNotSupportedError,
)
from myntos.skattemelding.models import (
    SkattemeldingFiling,
    SkattemeldingFilingDocument,
    SkattemeldingStatus,
)
from myntos.skattemelding.naeringsspesifikasjon import (
    AccountFigures,
    Grunnlag,
    Naeringsspesifikasjon,
    build,
    document,
)

# Enhetsregisteret's rollegruppe for the revisor.
_AUDITOR_GROUP = "REVI"


def supported_years() -> list[int]:
    """The income years Myntos has Skatteetaten's schemas and code lists for."""
    return sorted(
        set(schema.NAERINGSSPESIFIKASJON) & set(schema.SKATTEMELDING_UPERSONLIG) & codes.YEARS
    )


def require_supported(year: int) -> None:
    if year not in supported_years():
        years = ", ".join(str(y) for y in supported_years())
        raise SkattemeldingYearNotSupportedError(
            f"Myntos lager skattemeldingen for inntektsårene {years}, ikke for {year}"
        )


def last_day(company: Company, year: int) -> date:
    """The income year's last day: 31 December, or a wound-up company's last day."""
    wound_up = company.wound_up_on
    if wound_up is not None and wound_up.year == year:
        return wound_up
    return date(year, 12, 31)


def is_final(company: Company, year: int) -> bool:
    """The wound-up company's last year, whose skattemelding is a forhåndsfastsetting."""
    return company.wound_up_on is not None and company.wound_up_on.year == year


def is_over(company: Company, year: int, today: date) -> bool:
    """Whether the income year has ended, so its skattemelding may be checked and filed."""
    return today > last_day(company, year)


@dataclass(frozen=True)
class Auditor:
    """What Enhetsregisteret, as the company page last fetched it, says about a revisor."""

    #: None when Myntos has no register answer for the company.
    has_auditor: bool | None
    name: str | None = None
    #: The register has deleted the company and lists no roles for it, so the
    #: person answers instead (``revisor`` in the check's body).
    deleted: bool = False
    #: The person's answer, for a deleted company.
    answered: bool = False


async def auditor(db: AsyncSession, company: Company, *, answer: bool | None = None) -> Auditor:
    """Whether the company has a revisor, from the register, or from ``answer`` when it cannot say.

    A deleted company's role list comes back empty (the register answers 404
    for it), which would read as "no revisor"; that is a guess, so it is
    ``None`` until the person answers.
    """
    snapshot = await ledger_service.get_register_snapshot(db, company=company)
    if snapshot is None:
        return Auditor(has_auditor=None)
    for role in snapshot.roles or []:
        if role.get("group") == _AUDITOR_GROUP and not role.get("resigned"):
            name = role.get("name")
            return Auditor(has_auditor=True, name=name if isinstance(name, str) else None)
    if isinstance(snapshot.unit, dict) and snapshot.unit.get("deleted_on"):
        return Auditor(has_auditor=answer, deleted=True, answered=answer is not None)
    return Auditor(has_auditor=False)


def _auditor_findings(found: Auditor) -> list[Finding]:
    if found.deleted and found.has_auditor is None:
        return [
            merknad(
                "revisor_ukjent_slettet",
                "Selskapet er slettet fra Enhetsregisteret, som da ikke lenger viser hvem som "
                "hadde roller i det. Myntos vet derfor ikke om selskapet hadde revisor, og spør "
                "deg når du kontrollerer skattemeldingen.",
            )
        ]
    if found.has_auditor is None:
        return [
            feil(
                "revisor_ukjent",
                "Myntos vet ikke om selskapet har revisor. Hent opplysningene fra "
                "Enhetsregisteret under Selskap, så ser Myntos det der. Det avgjør om "
                "revisoren skal bekrefte næringsspesifikasjonen før Skatteetaten behandler den.",
            )
        ]
    if found.has_auditor and found.answered:
        return [
            merknad(
                "revisor_bekrefter",
                "Du har oppgitt at selskapet har revisor. Når du har sendt, må revisoren bekrefte "
                "næringsspesifikasjonen i Skatteetatens løsning før Skatteetaten behandler "
                "skattemeldingen.",
            )
        ]
    if found.has_auditor:
        return [
            merknad(
                "revisor_bekrefter",
                f"Enhetsregisteret oppgir {found.name or 'en revisor'} som revisor. Når du har "
                "sendt, må revisoren bekrefte næringsspesifikasjonen i Skatteetatens løsning "
                "før Skatteetaten behandler skattemeldingen.",
            )
        ]
    return []


async def grunnlag(
    db: AsyncSession, *, company: Company, year: int, user: User, found: Auditor
) -> Grunnlag:
    """The year's figures from the books, as plain values."""
    financial_year = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if financial_year is None:
        raise FinancialYearNotFoundError(f"selskapet har ikke noe regnskapsår {year} i Myntos")
    snapshot = await saft_service.load(
        db,
        company=company,
        year=year,
        exported_by=user,
        software_version=__version__,
        created_at=utc_now(),
    )
    accounts = tuple(
        AccountFigures(
            number=account.account_id,
            name=account.description,
            category=account.grouping_category,
            code=account.grouping_code,
            opening=account.opening.net,
            closing=account.closing.net,
        )
        for account in snapshot.audit.accounts
    )
    return Grunnlag(
        year=year,
        start=financial_year.start_date,
        end=financial_year.end_date,
        organisation_form=company.organisation_form,
        year_closed=financial_year.closed_at is not None,
        has_auditor=bool(found.has_auditor),
        accounts=accounts,
        saldo=await saldo_module.specification(db, company=company, year=year),
        wound_up=is_final(company, year),
    )


@dataclass(frozen=True)
class Prepared:
    """A year's næringsspesifikasjon as the books have it now, and everything said about it."""

    nsp: Naeringsspesifikasjon
    auditor: Auditor
    #: The builder's findings, the revisor's, and the formue's, in that order.
    findings: tuple[Finding, ...]
    formue: upersonlig.Formue

    @property
    def blocked(self) -> bool:
        return any(finding.severity is Severity.FEIL for finding in self.findings)


async def prepare(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    user: User,
    auditor_answer: bool | None = None,
) -> Prepared:
    require_supported(year)
    found = await auditor(db, company, answer=auditor_answer)
    nsp = build(await grunnlag(db, company=company, year=year, user=user, found=found))
    formue, formue_findings = upersonlig.formue(nsp)
    return Prepared(
        nsp=nsp,
        auditor=found,
        findings=(*nsp.findings, *_auditor_findings(found), *formue_findings),
        formue=formue,
    )


def naeringsspesifikasjon_xml(nsp: Naeringsspesifikasjon, *, partsnummer: int) -> bytes:
    return xmlwriter.write(
        schema.naeringsspesifikasjon(nsp.year), document(nsp, partsreferanse=partsnummer)
    )


def content_hash(nsp_xml: bytes) -> str:
    """SHA-256 of the næringsspesifikasjon, which carries no timestamp or reference."""
    return hashlib.sha256(nsp_xml).hexdigest()


# --- Filings ------------------------------------------------------------------------------


async def filings_for(db: AsyncSession, company: Company, year: int) -> list[SkattemeldingFiling]:
    """The year's filings, newest first."""
    return list(
        await db.scalars(
            select(SkattemeldingFiling)
            .where(SkattemeldingFiling.company_id == company.id, SkattemeldingFiling.year == year)
            .options(selectinload(SkattemeldingFiling.documents))
            .order_by(SkattemeldingFiling.id.desc())
        )
    )


def latest_filed(filings: Sequence[SkattemeldingFiling]) -> SkattemeldingFiling | None:
    """The newest filing that was sent, which is the one Skatteetaten holds."""
    return next(
        (f for f in filings if SkattemeldingStatus(f.status).is_filed),
        None,
    )


async def get_filing(
    db: AsyncSession, company: Company, filing_id: int, *, lock: bool = False
) -> SkattemeldingFiling:
    """One filing, through its company. ``lock`` for the reason MVA's ``get_filing`` gives."""
    query = (
        select(SkattemeldingFiling)
        .where(SkattemeldingFiling.id == filing_id, SkattemeldingFiling.company_id == company.id)
        .options(selectinload(SkattemeldingFiling.documents))
    )
    if lock:
        query = query.with_for_update(of=SkattemeldingFiling)
    filing = await db.scalar(query)
    if filing is None:
        raise SkattemeldingFilingNotFoundError("finner ikke skattemeldingen")
    return filing


def answers_json(answers: upersonlig.Answers, *, forhaandsfastsetting: bool) -> dict[str, Any]:
    """What the person said, and what kind of melding it went into, as the filing keeps it."""
    return {
        "ytelser_til_aksjonaerer": answers.benefits_with_shareholders,
        "fullt_avviklet": answers.wound_up,
        "likvidasjonsvederlag": answers.liquidation_payout,
        "forhaandsfastsetting": forhaandsfastsetting,
    }


def payout_from(stored: dict[str, Any]) -> int | None:
    """The payout the person stated, from a filing's answers; None before there was one."""
    payout = stored.get("likvidasjonsvederlag")
    return payout if isinstance(payout, int) else None


def answers_from(stored: dict[str, Any]) -> upersonlig.Answers:
    return upersonlig.Answers(
        benefits_with_shareholders=bool(stored["ytelser_til_aksjonaerer"]),
        wound_up=bool(stored.get("fullt_avviklet")),
        liquidation_payout=payout_from(stored),
    )


async def record_validation(
    db: AsyncSession,
    *,
    company: Company,
    prepared: Prepared,
    melding: FetchedMelding,
    partsnummer: int,
    answers: upersonlig.Answers,
    nsp_xml: bytes,
    request_xml: bytes,
    validation_xml: bytes,
    result: Validering,
    user_id: int | None,
    forhaandsfastsetting: bool = False,
) -> SkattemeldingFiling:
    nsp = prepared.nsp
    filing = SkattemeldingFiling(
        company_id=company.id,
        year=nsp.year,
        status=(
            SkattemeldingStatus.VALIDERT if result.accepted else SkattemeldingStatus.AVVIST
        ).value,
        melding_id=melding.id[:64],
        partsnummer=partsnummer,
        request_xml=request_xml.decode("utf-8"),
        content_hash=content_hash(nsp_xml),
        answers=answers_json(answers, forhaandsfastsetting=forhaandsfastsetting),
        has_auditor=bool(prepared.auditor.has_auditor),
        aarsresultat=nsp.result,
        skattemessig_resultat=nsp.taxable_result,
        beregnet_skatt=result.computed_tax,
        findings=[
            {"severity": f.severity.value, "code": f.code, "message": f.message}
            for f in prepared.findings
        ],
        validation_status=result.result[:64],
        validation_xml=validation_xml.decode("utf-8", errors="replace"),
        validated_at=utc_now(),
        created_by_user_id=user_id,
        documents=[],
    )
    db.add(filing)
    await db.flush()
    return filing


async def record_document(
    db: AsyncSession,
    filing: SkattemeldingFiling,
    *,
    data_type: str,
    filename: str,
    content_type: str,
    content: bytes,
    storage_key: str,
) -> SkattemeldingFilingDocument:
    stored = SkattemeldingFilingDocument(
        filing_id=filing.id,
        data_type=data_type,
        filename=filename,
        content_type=content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
        fetched_at=utc_now(),
    )
    filing.documents.append(stored)
    await db.flush()
    return stored
