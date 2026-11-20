"""Who may register a company with the access point and send its invoices.

Two acts, with different people behind them. Registering is an admin's, once
per company, and ends on the access point's own page where somebody who signs
for the company proves who they are. Sending is anybody's who may write the
books, once per invoice, and for a key it always waits for a person's yes
(``key_send`` on the route): an invoice that has gone cannot be called back.

The access point answers to the deployment's key whatever company it names, so
every check of *who may send as this business* is made here, before it is asked:
the company must have been confirmed through Altinn before it can be
registered, and registered and checked before anything is sent.

Nothing here calls the access point. The endpoints in myntos.api.v1.peppol do,
between these checks and the storing of what came back, the way every other
counterparty is called (docs/ARCHITECTURE.md, the dependency rule under
«Backend layout»): a request made from inside the domain is what that rule
exists to stop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.faktura.errors import InvoiceNotFoundError
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.ledger.models import Company
from myntos.domain.peppol.errors import (
    AlreadySentError,
    CompanyAddressMissingError,
    CompanyNotConfirmedError,
    NotRegisteredError,
    NotSendableError,
    TransmissionNotFoundError,
)
from myntos.domain.peppol.models import (
    PeppolRegistration,
    PeppolTransmission,
    TransmissionStatus,
)

# --- Registration ------------------------------------------------------------------


@dataclass(frozen=True)
class Standing:
    """Whether the company can send, and if not, which step is missing."""

    # The deployment has an access point at all.
    available: bool
    # Confirmed through Altinn, which registering requires.
    company_confirmed: bool
    registration: PeppolRegistration | None

    @property
    def registered(self) -> bool:
        return self.registration is not None

    @property
    def verified(self) -> bool:
        return self.registration is not None and self.registration.is_verified

    @property
    def can_send(self) -> bool:
        return self.available and self.verified


async def get_registration(db: AsyncSession, *, company: Company) -> PeppolRegistration | None:
    registration: PeppolRegistration | None = await db.scalar(
        select(PeppolRegistration).where(PeppolRegistration.company_id == company.id)
    )
    return registration


def mark_verified(registration: PeppolRegistration) -> None:
    """Stamp the access point's identity check as done.

    Stored once and never asked about again: the person finishes the check on
    another site, so until then every read asks, and after it nothing needs to.
    """
    if registration.verified_at is None:
        registration.verified_at = utc_now()


def require_registrable(company: Company) -> None:
    """Refuse a company that has not been confirmed through Altinn.

    Without that, a new account that typed somebody else's orgnr would be one
    click from sending invoices in their name: the access point's own identity
    check would then be the only wall, and the link to it would be in the wrong
    person's hands.
    """
    if not company.is_verified:
        raise CompanyNotConfirmedError(
            "selskapet må bekreftes med ID-porten før det kan registreres for e-faktura, "
            "så bare de som kan handle for selskapet kan sende fakturaer i dets navn"
        )


def registered_address(company: Company) -> tuple[str, str, str]:
    """The business address the access point registers the company under."""
    if not (company.address_line and company.postal_code and company.city):
        raise CompanyAddressMissingError(
            "tilgangspunktet trenger selskapets forretningsadresse. "
            "Hent opplysningene fra Enhetsregisteret på nytt under Selskap"
        )
    return company.address_line, company.postal_code, company.city


async def record_registration(
    db: AsyncSession,
    *,
    company: Company,
    registration: PeppolRegistration | None,
    remote_company_id: str,
    verified: bool,
) -> PeppolRegistration:
    """Keep the company's record at the access point, found or created."""
    if registration is None:
        registration = PeppolRegistration(
            company_id=company.id, remote_company_id=remote_company_id
        )
        db.add(registration)
    registration.remote_company_id = remote_company_id
    if verified:
        mark_verified(registration)
    await db.flush()
    return registration


# --- Sending -----------------------------------------------------------------------


@dataclass(frozen=True)
class Sendable:
    """An issued document that may go, and the registration it goes under."""

    document: SalesDocument
    registration: PeppolRegistration
    xml: str
    buyer_orgnr: str


async def prepare_send(db: AsyncSession, *, company: Company, document_pk: int) -> Sendable:
    """Check that the document may go, holding its row until the request ends.

    The document row is locked first, so two presses of the button in two tabs
    cannot both get past the check for an earlier transmission: the second waits
    for the first to commit, then finds it.

    The registration may still be waiting for the identity check. Whether it
    has been finished since is the access point's to say, so the caller asks
    and then calls :func:`require_verified`.
    """
    document = await db.scalar(
        select(SalesDocument)
        .where(SalesDocument.id == document_pk, SalesDocument.company_id == company.id)
        .with_for_update()
    )
    if document is None:
        raise InvoiceNotFoundError("finner ikke fakturaen")
    if not document.is_issued or document.ehf_xml is None:
        raise NotSendableError(
            "fakturaen er ikke utstedt ennå. Utsted den først, så sendes EHF-en som ble laget da"
        )
    if not document.buyer_orgnr:
        raise NotSendableError(
            "kunden har ikke organisasjonsnummer på fakturaen, og over Peppol er det adressen. "
            "Send PDF-en til kunden selv"
        )

    registration = await get_registration(db, company=company)
    if registration is None:
        raise NotRegisteredError(
            "selskapet er ikke registrert for e-faktura ennå. "
            "En administrator gjør det under Selskap, E-faktura"
        )

    earlier = await db.scalar(
        select(PeppolTransmission).where(
            PeppolTransmission.document_pk == document.id,
            PeppolTransmission.status != TransmissionStatus.FAILED,
        )
    )
    if earlier is not None:
        raise AlreadySentError(
            "fakturaen er allerede sendt som EHF. En faktura som sendes to ganger, "
            "kan bli betalt to ganger"
        )
    return Sendable(
        document=document,
        registration=registration,
        xml=document.ehf_xml,
        buyer_orgnr=document.buyer_orgnr,
    )


def require_verified(registration: PeppolRegistration) -> None:
    if not registration.is_verified:
        raise NotRegisteredError(
            "identitetskontrollen hos tilgangspunktet er ikke fullført. "
            "Den som signerer for selskapet fullfører den fra Selskap, E-faktura"
        )


async def record_transmission(
    db: AsyncSession,
    *,
    company: Company,
    document: SalesDocument,
    remote_document_id: str,
    recipient: str,
    status: TransmissionStatus,
    failure_category: str | None,
    failure_message: str | None,
) -> PeppolTransmission:
    """Keep what the access point answered to a send."""
    transmission = PeppolTransmission(
        company_id=company.id,
        document_pk=document.id,
        remote_document_id=remote_document_id,
        recipient=recipient,
        status=status,
        failure_category=failure_category,
        failure_message=failure_message,
        sent_at=utc_now(),
    )
    db.add(transmission)
    await db.flush()
    return transmission


async def latest_transmissions(
    db: AsyncSession, *, company: Company, document_pks: Sequence[int]
) -> dict[int, PeppolTransmission]:
    """The most recent transmission of each document, as stored."""
    if not document_pks:
        return {}
    rows = await db.scalars(
        select(PeppolTransmission)
        .where(
            PeppolTransmission.company_id == company.id,
            PeppolTransmission.document_pk.in_(document_pks),
        )
        .order_by(PeppolTransmission.id)
    )
    # Ascending, so the last one written for each document wins.
    return {row.document_pk: row for row in rows}


async def latest_transmission(
    db: AsyncSession, *, company: Company, document_pk: int
) -> PeppolTransmission:
    """The document's latest transmission, as stored."""
    exists = await db.scalar(
        select(SalesDocument.id).where(
            SalesDocument.id == document_pk, SalesDocument.company_id == company.id
        )
    )
    if exists is None:
        raise InvoiceNotFoundError("finner ikke fakturaen")
    latest = (await latest_transmissions(db, company=company, document_pks=[document_pk])).get(
        document_pk
    )
    if latest is None:
        raise TransmissionNotFoundError("fakturaen er ikke sendt som EHF")
    return latest


async def record_delivery(
    db: AsyncSession,
    transmission: PeppolTransmission,
    *,
    status: TransmissionStatus,
    failure_category: str | None,
    failure_message: str | None,
) -> None:
    """Keep a fresh answer about a pending transmission, and when it came."""
    transmission.status = status
    transmission.failure_category = failure_category
    transmission.failure_message = failure_message
    transmission.status_checked_at = utc_now()
    await db.flush()
