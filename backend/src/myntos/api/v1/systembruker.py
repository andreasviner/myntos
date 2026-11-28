"""Altinn 3 systembruker endpoints: check status, request approval, and refresh.

A systembruker authorizes Myntos to call machine-to-machine Skatteetaten APIs
(such as Aksjonærregisteroppgaven RF-1086) on a company's behalf without a
person re-authenticating through ID-porten on every call. The company's own
representative approves the request in Altinn's access-management portal via
``confirm_url``; Myntos cannot approve on their behalf.

``GET /companies/{company_id}/altinn/systembruker`` is open to agent keys
(``KEY_READ``) so an agent preparing RF-1086 can tell whether the company has
approved a system user yet. Creating and refreshing a system-user request are
closed to keys (``_GOVERNMENT``), because granting Myntos authority in Altinn
is the company administrator's own act.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession
from myntos.auth.maskinporten.client import MaskinportenError, get_token_provider
from myntos.core.config import Settings, get_settings
from myntos.domain.altinn import service as systemuser_service
from myntos.domain.altinn.errors import (
    InvalidSystemUserResourceError,
    SystemUserNotAvailableError,
    SystemUserRemoteError,
    SystemUserRequestNotFoundError,
)
from myntos.domain.altinn.models import CompanySystemUser
from myntos.domain.identity.access import Permission, require_permission
from myntos.integrations.altinn import systemregister, systemuser

router = APIRouter(tags=["altinn"])
logger = logging.getLogger(__name__)

# Resources requested by default when a company connects Myntos in Altinn:
# narrow to what Myntos actually calls through systembruker today so a customer
# is never asked to approve unused privileges.
DEFAULT_RESOURCES: tuple[str, ...] = (
    systemregister.AKSJONAERREGISTER_RESOURCE,
    systemregister.AARSREGNSKAP_RESOURCE,
)

# Human-readable bokmål labels for the declared resources, shown on the page.
RESOURCE_LABELS: dict[str, str] = {
    systemregister.AKSJONAERREGISTER_RESOURCE: "Aksjonærregisteroppgaven (RF-1086)",
    systemregister.AARSREGNSKAP_RESOURCE: "Årsregnskap til Regnskapsregisteret (RR-0002)",
    systemregister.SKATTEMELDING_RESOURCE: "Skattemelding og næringsspesifikasjon",
    systemregister.FRISTER_RESOURCE: "Frister hos Skatteetaten",
    systemregister.KRAV_OG_BETALINGER_RESOURCE: "Krav og betalinger hos Skatteetaten",
    systemregister.AMELDING_RESOURCE: "A-melding",
    systemregister.AMELDING_AVSTEMMING_RESOURCE: "A-melding avstemmingsinformasjon (A07)",
    systemregister.SKATTEKORT_RESOURCE: "Skattekort til arbeidsgiver",
    systemregister.RF1321_RESOURCE: "Betalinger til selvstendig næringsdrivende (RF-1321)",
}


def is_systemuser_available(settings: Settings | None = None) -> bool:
    """Whether this deployment has Maskinporten and Altinn vendor config for systembruker."""
    settings = settings or get_settings()
    has_mp_key = (
        settings.maskinporten_key_material is not None or settings.maskinporten_key_file is not None
    )
    return bool(
        (settings.maskinporten_client_id or "").strip()
        and has_mp_key
        and settings.altinn_vendor_orgnr.strip()
        and settings.altinn_system_name.strip()
        and settings.altinn_system_redirect_url.strip().startswith("https://")
    )


class SystemUserItemResponse(BaseModel):
    id: int
    system_id: str
    status: str
    approved: bool
    resources: list[str]
    access_packages: list[str]
    confirm_url: str | None
    requested_at: datetime
    checked_at: datetime | None
    approved_at: datetime | None


class SystemUserStatusResponse(BaseModel):
    company_id: int
    available: bool
    approved: bool
    system_id: str | None
    system_user: SystemUserItemResponse | None
    resource_labels: dict[str, str]


class CreateSystemUserBody(BaseModel):
    # Optional subset of systemregister.SYSTEM_RESOURCES; defaults to RF-1086.
    resources: list[str] | None = Field(default=None, min_length=1, max_length=16)


def _to_item(row: CompanySystemUser) -> SystemUserItemResponse:
    return SystemUserItemResponse(
        id=row.id,
        system_id=row.system_id,
        status=row.status,
        approved=row.is_approved,
        resources=list(row.resources),
        access_packages=list(row.access_packages),
        confirm_url=row.confirm_url,
        requested_at=row.requested_at,
        checked_at=row.checked_at,
        approved_at=row.approved_at,
    )


def _status_response(
    company_id: int,
    row: CompanySystemUser | None,
    settings: Settings,
) -> SystemUserStatusResponse:
    available = is_systemuser_available(settings)
    sys_id: str | None = None
    if available:
        try:
            sys_id = systemregister.system_id(settings)
        except systemregister.NotConfiguredError:
            available = False
    return SystemUserStatusResponse(
        company_id=company_id,
        available=available,
        approved=row.is_approved if row is not None else False,
        system_id=sys_id,
        system_user=_to_item(row) if row is not None else None,
        resource_labels=dict(RESOURCE_LABELS),
    )


@router.get(
    "/companies/{company_id}/altinn/systembruker",
    openapi_extra=KEY_READ,
)
async def get_system_user_status(
    context: CurrentCompany,
    db: DbSession,
) -> SystemUserStatusResponse:
    """Read the company's Altinn 3 systembruker connection status.

    Returns whether this deployment supports Altinn 3 systembruker
    (``available``) and whether the company has an approved system user
    (``approved``), along with the pending confirmation URL if a request is
    waiting for approval in Altinn.

    In Norwegian accounting, a systembruker (Altinn 3 system user) authorizes
    Myntos to communicate with Skatteetaten APIs such as
    Aksjonærregisteroppgaven (RF-1086) on the company's behalf. Read-only; does
    not contact Altinn. If ``approved`` is false, ask a company administrator
    to create and approve the system user under Innstillinger for selskapet or
    on the Aksjonærregisteroppgave page.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    row = await systemuser_service.latest_for_company(db, context.company)
    return _status_response(context.company.id, row, get_settings())


@router.post(
    "/companies/{company_id}/altinn/systembruker",
    status_code=status.HTTP_201_CREATED,
)
async def create_system_user_request(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    body: CreateSystemUserBody | None = None,
) -> SystemUserStatusResponse:
    """Create an Altinn 3 systembruker approval request for the company.

    Contacts Altinn's system-user vendor API with a Maskinporten token and
    returns a ``confirm_url`` where a representative with access-management
    rights in Altinn approves Myntos for the requested resources (defaulting to
    ``ske-innrapportering-aksjonaerregisteroppgave`` for RF-1086).

    Requires ``organization:manage`` (owner or admin). Refuses with 503
    ``system_user_not_available`` when Maskinporten or Altinn vendor settings
    are not configured, 422 ``invalid_system_user_resource`` if an unknown
    resource is requested, and 502 ``system_user_remote`` if Altinn or
    Maskinporten rejects the request. Closed to API keys.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    settings = get_settings()
    if not is_systemuser_available(settings):
        raise SystemUserNotAvailableError(
            "Altinn-systembruker er ikke satt opp i denne installasjonen"
        )

    allowed = set(systemregister.SYSTEM_RESOURCES)
    requested_resources = tuple(body.resources) if body and body.resources else DEFAULT_RESOURCES
    unknown = [res for res in requested_resources if res not in allowed]
    if unknown:
        raise InvalidSystemUserResourceError(
            "ukjent Altinn-ressurs i forespørselen om systembruker"
        )

    external_ref = systemuser_service.new_external_ref(context.company)
    try:
        token = await get_token_provider().get_access_token([systemuser.SCOPE_WRITE])
        created = await systemuser.request_system_user(
            token,
            customer_orgnr=context.company.orgnr,
            external_ref=external_ref,
            resources=requested_resources,
            settings=settings,
        )
    except (
        MaskinportenError,
        systemuser.SystemUserError,
        systemregister.NotConfiguredError,
    ) as err:
        logger.error("Altinn system-user request failed for company %s", context.company.id)
        raise SystemUserRemoteError(
            "Altinn avslo forespørselen om systembruker. Kontroller organisasjonsnummeret og "
            "prøv igjen."
        ) from err

    row = await systemuser_service.record_request(
        db,
        company=context.company,
        system_id=created.system_id or systemregister.system_id(settings),
        external_ref=created.external_ref or external_ref,
        altinn_request_id=created.id,
        status=created.status,
        resources=requested_resources,
        access_packages=(),
        confirm_url=created.confirm_url,
        requested_by_user_id=user.id,
    )
    return _status_response(context.company.id, row, settings)


@router.post("/companies/{company_id}/altinn/systembruker/oppdater")
async def refresh_system_user_status(
    context: CurrentCompany,
    db: DbSession,
) -> SystemUserStatusResponse:
    """Poll Altinn for the current approval status of the company's systembruker request.

    Call this after the company's representative has approved the request on
    Altinn's ``confirm_url`` page. Updates the stored status to ``accepted``
    (or Altinn's latest status) so RF-1086 submission can proceed.

    Requires ``organization:manage`` (owner or admin). Refuses with 404
    ``system_user_request_not_found`` if no request has been created yet, 503
    ``system_user_not_available`` if unconfigured, and 502
    ``system_user_remote`` if Altinn or Maskinporten cannot be reached. Closed
    to API keys.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    settings = get_settings()
    if not is_systemuser_available(settings):
        raise SystemUserNotAvailableError(
            "Altinn-systembruker er ikke satt opp i denne installasjonen"
        )

    row = await systemuser_service.latest_for_company(db, context.company)
    if row is None:
        raise SystemUserRequestNotFoundError(
            "det finnes ingen forespørsel om systembruker i Altinn for dette selskapet ennå"
        )

    try:
        token = await get_token_provider().get_access_token([systemuser.SCOPE_READ])
        remote = await systemuser.get_request(token, row.altinn_request_id, settings=settings)
    except (MaskinportenError, systemuser.SystemUserError) as err:
        logger.error("Altinn system-user status refresh failed for company %s", context.company.id)
        raise SystemUserRemoteError(
            "klarte ikke å hente status for systembruker fra Altinn"
        ) from err

    await systemuser_service.update_status(
        db, row, status=remote.status, confirm_url=remote.confirm_url
    )
    return _status_response(context.company.id, row, settings)
