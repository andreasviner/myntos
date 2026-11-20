"""Frister: the company's deadlines with Skatteetaten and Brønnøysund, and which are met.

``GET /companies/{id}/frister``
    Every deadline in the coming year, and the ones known to be missed in the
    year behind. Read-only, and stores nothing: the list is computed from the
    filings, the register answer and the company's settings each time.

How the list is made, and why a deadline Myntos cannot see the answer to is
«not known» rather than missed, is in :mod:`myntos.domain.frister`.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession
from myntos.core.clock import utc_now
from myntos.domain.frister import service
from myntos.domain.identity.access import Permission, require_permission

router = APIRouter(tags=["frister"])

# A deadline is a Norwegian calendar day: between midnight in Oslo and
# midnight UTC the day after a deadline is still the deadline for a UTC clock.
_OSLO = ZoneInfo("Europe/Oslo")


def _today() -> date:
    return utc_now().astimezone(_OSLO).date()


class FristResponse(BaseModel):
    kind: str
    label: str
    deadline: date
    year: int
    recipient: str
    files_from_myntos: bool
    done: bool | None
    done_by: str | None
    is_overdue: bool
    note: str | None
    termin: int | None
    naering: str | None


class FristerResponse(BaseModel):
    company_id: int
    today: date
    until: date
    organisation_form: str | None
    covers_yearly: bool
    vat_registered: bool
    register_checked_at: datetime | None
    wound_up_on: date | None
    frister: list[FristResponse]


@router.get("/companies/{company_id}/frister", openapi_extra=KEY_READ)
async def frister(context: CurrentCompany, db: DbSession) -> FristerResponse:
    """The company's deadlines with the authorities for the coming year, and which are met.

    A Norwegian company owes the state a handful of reports and payments on
    fixed dates, and missing one costs a fee (gebyr) or an extra tax
    (tilleggsskatt). This lists them for one company, soonest first, from
    ``today`` (in Norway) to ``until``, a year later. Behind ``today`` it lists
    only what is known to be missed, for at most a year.

    Each row of ``frister``:

    - ``kind``: what is due. ``mva_melding`` (the VAT return for one period,
      «termin»), ``skattemelding`` (the yearly tax return),
      ``arsregnskap`` (the annual accounts, to Brønnøysund's
      Regnskapsregisteret), ``aksjonaerregisteroppgave`` (the yearly report of
      who owns the shares) or ``forskuddsskatt`` (a payment of advance tax).
    - ``label``: how a person reads it, in Norwegian. ``deadline``: the last
      day. A VAT deadline on a weekend is moved to the Monday; public holidays
      are not modelled, so a date is never later than the real one.
    - ``year``: the year the duty is about (the income year, or the VAT
      period's year), not the year the date falls in. ``termin`` is the VAT
      period's number, which every ``/mva`` route takes with ``aar``; and for
      a company that files two VAT returns, ``naering`` names which
      (``alminnelig`` or ``primaernaering``).
    - ``recipient``: ``skatteetaten`` or ``bronnoysund``.
    - ``files_from_myntos``: whether it can be filed from Myntos today: the
      VAT return (``/mva``) and an AS's skattemelding (``/skattemelding``).
      Everything else is done somewhere else; ``note`` says what to know.
    - ``done``: ``true`` when Myntos knows it is met, and ``done_by`` says who
      says so: ``myntos`` (filed from Myntos), ``annet_program`` (the person
      recorded that another program filed the VAT period) or
      ``regnskapsregisteret`` (Brønnøysund lists the accounts as received).
      ``false`` when Myntos knows it is not: a VAT period inside the books with
      nothing filed, or accounts Brønnøysund had not received when it was last
      asked after the deadline. ``null`` when Myntos cannot know, which is
      most of them: a payment, or something filed from another program. Never
      treat ``null`` as missed.
    - ``is_overdue``: ``done`` is false and the deadline has passed.

    ``covers_yearly`` false means the company's ``organisation_form`` is one
    whose yearly deadlines Myntos does not list yet (only AS, ASA and ENK
    are), so the list has the VAT periods and nothing else. ``vat_registered``
    false means there are no VAT periods. ``register_checked_at`` is when
    Brønnøysund was last asked, which is how fresh an ``arsregnskap`` answer
    is. ``wound_up_on`` set means the books end on that day: the last year's
    skattemelding goes before the company is deleted, on no fixed date, so it
    is not in the list.

    Not in the list: the a-melding (due monthly for a company that pays wages,
    which Myntos does not know), and dates Skatteetaten has moved for this
    company. Needs ``ledger:read``, which every role has; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    today = _today()
    found = await service.for_company(db, company, today)
    return FristerResponse(
        company_id=company.id,
        today=found.today,
        until=found.until,
        organisation_form=company.organisation_form,
        covers_yearly=found.covers_yearly,
        vat_registered=company.vat_registered,
        register_checked_at=found.register_checked_at,
        wound_up_on=found.wound_up_on,
        frister=[
            FristResponse(
                kind=frist.kind.value,
                label=frist.label,
                deadline=frist.deadline,
                year=frist.year,
                recipient=frist.recipient.value,
                files_from_myntos=frist.files_from_myntos,
                done=frist.done,
                done_by=frist.done_by,
                is_overdue=frist.is_overdue(today),
                note=frist.note,
                termin=frist.termin,
                naering=frist.naering,
            )
            for frist in found.frister
        ],
    )
