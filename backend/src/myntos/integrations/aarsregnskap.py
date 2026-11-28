"""Altinn 3 client for Brønnøysundregistrene Årsregnskap (RR-0002).

Targets Brønnøysundregistrene's Altinn 3 app ``brg/aarsregnskap-vanlig-202406``
(verified against live ``applicationmetadata`` on ``brg.apps.altinn.no`` and
``brg.apps.tt02.altinn.no``, 2026-11-28):

1. ``POST /instances`` with ``{"instanceOwner": {"organisationNumber": orgnr}}``
   auto-creates one ``Hovedskjema`` element and one ``Underskjema`` element.
2. ``PUT /instances/{party}/{guid}/data/{hovedskjema_id}`` uploads RR-0002 XML
   with ``Content-Type: application/xml``.
3. ``PUT /instances/{party}/{guid}/data/{underskjema_id}`` uploads RR-0002-U XML
   with ``Content-Type: application/xml``.
4. Optional ``POST /instances/{party}/{guid}/data?dataType=Vedlegg`` uploads
   PDF attachments (e.g., annual accounts + notes PDF or auditor's report).
5. ``GET /instances/{party}/{guid}/validate`` runs Brønnøysund's server-side
   validation rules.
6. ``PUT /instances/{party}/{guid}/process/next`` with ``{"action": "confirm"}``
   locks the instance and moves it from ``Utfylling`` to ``Signering``. Steps
   1-6 may use either an Altinn 3 systembruker token or an ID-porten token.
7. ``PUT /instances/{party}/{guid}/process/next`` with ``{"action": "sign"}``
   signs and automatically submits the annual accounts to Regnskapsregisteret.
   Step 7 **must** use a person's ID-porten-derived Altinn token (never a
   systembruker token), because the signature legally binds a fødselsnummer.
8. Once ``process.ended`` is true, ``ref-data-as-pdf`` (and ``signature`` if
   present) are downloaded for five-year archiving.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from myntos.integrations.altinn.instances import (
    AltinnInstanceError,
    AppInstanceClient,
    Instance,
    InstanceRef,
)

# Data type names verified verbatim against brg/aarsregnskap-vanlig-202406 applicationmetadata.
DATA_TYPE_HOVEDSKJEMA = "Hovedskjema"
DATA_TYPE_UNDERSKJEMA = "Underskjema"
DATA_TYPE_VEDLEGG = "Vedlegg"
DATA_TYPE_RECEIPT_PDF = "ref-data-as-pdf"
DATA_TYPE_SIGNATURE = "signature"

# Process actions declared in brg/aarsregnskap-vanlig-202406 BPMN process.
ACTION_CONFIRM = "confirm"
ACTION_SIGN = "sign"
ACTION_REJECT = "reject"

# Altinn 3 validation severity 1 = Error (blocks process transition).
SEVERITY_ERROR = 1

XML_CONTENT_TYPE = "application/xml"


class AarsregnskapClientError(Exception):
    """Brønnøysundregistrene's Altinn 3 app refused a step or could not be reached.

    Carries whatever instance and data ids were already created before failure
    so callers can persist a partial-failure record instead of losing track of
    a live Altinn instance.
    """

    def __init__(
        self,
        message: str,
        *,
        instance_id: str | None = None,
        hovedskjema_data_id: str | None = None,
        underskjema_data_id: str | None = None,
        portal_url: str | None = None,
        validation_issues: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.instance_id = instance_id
        self.hovedskjema_data_id = hovedskjema_data_id
        self.underskjema_data_id = underskjema_data_id
        self.portal_url = portal_url
        self.validation_issues = validation_issues or []


@dataclass(frozen=True)
class PreparedInstance:
    """Result of instantiating, filling, validating, and optionally locking RR-0002."""

    ref: InstanceRef
    hovedskjema_data_id: str
    underskjema_data_id: str
    portal_url: str
    current_task: str | None
    locked: bool
    validation_issues: list[dict[str, Any]]


@dataclass(frozen=True)
class DownloadedArtifact:
    """One binary artifact fetched back from Altinn (receipt PDF or signature JSON)."""

    document_type: str
    external_document_id: str
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class InstanceStatusResult:
    """Current state of an RR-0002 instance after signing or status refresh."""

    ref: InstanceRef
    current_task: str | None
    process_ended: bool
    artifacts: tuple[DownloadedArtifact, ...]


def portal_url_for(app_url: str, ref: InstanceRef) -> str:
    """Direct Altinn 3 web UI link where a board member can review or sign the instance."""
    return f"{app_url.rstrip('/')}/#/instance/{ref.path}"


def has_blocking_validation_errors(issues: list[dict[str, Any]]) -> bool:
    """Whether Brønnøysund's ``/validate`` returned any error-severity issue."""
    for issue in issues:
        severity = issue.get("severity")
        if severity == SEVERITY_ERROR or str(severity).lower() == "error":
            return True
    return False


class AarsregnskapClient:
    """High-level client for ``brg/aarsregnskap-vanlig-202406``."""

    def __init__(self, app_url: str) -> None:
        self._app_url = app_url.rstrip("/")
        self._apps = AppInstanceClient(self._app_url)

    @property
    def app_url(self) -> str:
        return self._app_url

    async def prepare_and_lock(
        self,
        *,
        organisation_number: str,
        hovedskjema_xml: str,
        underskjema_xml: str,
        token: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
        lock: bool = True,
    ) -> PreparedInstance:
        """Create instance, fill XMLs, upload attachments, validate, and optionally lock."""
        ref: InstanceRef | None = None
        hoved_id: str | None = None
        under_id: str | None = None
        url: str | None = None
        issues: list[dict[str, Any]] = []
        try:
            instance = await self._apps.create_instance(
                token=token,
                organisation_number=organisation_number,
            )
            ref = instance.ref
            url = portal_url_for(self._app_url, ref)

            hoved_el = instance.element_of_type(DATA_TYPE_HOVEDSKJEMA)
            under_el = instance.element_of_type(DATA_TYPE_UNDERSKJEMA)

            hoved_bytes = hovedskjema_xml.encode("utf-8")
            under_bytes = underskjema_xml.encode("utf-8")

            if hoved_el is not None:
                hoved_id = hoved_el.id
                await self._apps.replace_data(
                    ref,
                    token=token,
                    element_id=hoved_id,
                    content=hoved_bytes,
                    content_type=XML_CONTENT_TYPE,
                    filename="hovedskjema.xml",
                )
            else:
                uploaded_hoved = await self._apps.upload_data(
                    ref,
                    token=token,
                    data_type=DATA_TYPE_HOVEDSKJEMA,
                    content=hoved_bytes,
                    content_type=XML_CONTENT_TYPE,
                    filename="hovedskjema.xml",
                )
                hoved_id = uploaded_hoved.id

            if under_el is not None:
                under_id = under_el.id
                await self._apps.replace_data(
                    ref,
                    token=token,
                    element_id=under_id,
                    content=under_bytes,
                    content_type=XML_CONTENT_TYPE,
                    filename="selskapsregnskap.xml",
                )
            else:
                uploaded_under = await self._apps.upload_data(
                    ref,
                    token=token,
                    data_type=DATA_TYPE_UNDERSKJEMA,
                    content=under_bytes,
                    content_type=XML_CONTENT_TYPE,
                    filename="selskapsregnskap.xml",
                )
                under_id = uploaded_under.id

            if attachments:
                await self._apps.upload_attachments(
                    ref,
                    token=token,
                    data_type=DATA_TYPE_VEDLEGG,
                    files=attachments,
                )

            issues = await self._apps.validate_instance(ref, token=token)
            current_task = instance.current_task
            locked = False
            if lock and not has_blocking_validation_errors(issues):
                advanced = await self._apps.process_next(ref, token=token, action=ACTION_CONFIRM)
                current_task = advanced.current_task
                locked = True

            return PreparedInstance(
                ref=ref,
                hovedskjema_data_id=hoved_id,
                underskjema_data_id=under_id,
                portal_url=url,
                current_task=current_task,
                locked=locked,
                validation_issues=issues,
            )
        except AltinnInstanceError as err:
            raise AarsregnskapClientError(
                str(err),
                instance_id=ref.path if ref is not None else None,
                hovedskjema_data_id=hoved_id,
                underskjema_data_id=under_id,
                portal_url=url,
                validation_issues=issues,
            ) from err

    async def sign_and_submit(
        self,
        ref: InstanceRef,
        *,
        id_porten_altinn_token: str,
    ) -> InstanceStatusResult:
        """Sign and auto-submit an instance in ``Signering`` using an ID-porten Altinn token."""
        try:
            signed = await self._apps.process_next(
                ref, token=id_porten_altinn_token, action=ACTION_SIGN
            )
            fetched = await self._apps.get_instance(ref, token=id_porten_altinn_token)
            artifacts = await self._download_receipts(fetched, token=id_porten_altinn_token)
            return InstanceStatusResult(
                ref=ref,
                current_task=fetched.current_task or signed.current_task,
                process_ended=fetched.process_ended or signed.process_ended,
                artifacts=artifacts,
            )
        except AltinnInstanceError as err:
            raise AarsregnskapClientError(
                str(err),
                instance_id=ref.path,
                portal_url=portal_url_for(self._app_url, ref),
            ) from err

    async def unlock(self, ref: InstanceRef, *, token: str) -> Instance:
        """Move a locked instance from ``Signering`` back to ``Utfylling``."""
        try:
            return await self._apps.process_next(ref, token=token, action=ACTION_REJECT)
        except AltinnInstanceError as err:
            raise AarsregnskapClientError(
                str(err),
                instance_id=ref.path,
                portal_url=portal_url_for(self._app_url, ref),
            ) from err

    async def fetch_status(self, ref: InstanceRef, *, token: str) -> InstanceStatusResult:
        """Read current instance status and download receipt artifacts if ended."""
        try:
            fetched = await self._apps.get_instance(ref, token=token)
            artifacts = (
                await self._download_receipts(fetched, token=token) if fetched.process_ended else ()
            )
            return InstanceStatusResult(
                ref=ref,
                current_task=fetched.current_task,
                process_ended=fetched.process_ended,
                artifacts=artifacts,
            )
        except AltinnInstanceError as err:
            raise AarsregnskapClientError(
                str(err),
                instance_id=ref.path,
                portal_url=portal_url_for(self._app_url, ref),
            ) from err

    async def _download_receipts(
        self, instance: Instance, *, token: str
    ) -> tuple[DownloadedArtifact, ...]:
        artifacts: list[DownloadedArtifact] = []
        pdf_el = instance.element_of_type(DATA_TYPE_RECEIPT_PDF)
        if pdf_el is not None:
            data = await self._apps.download_data(instance.ref, token=token, element_id=pdf_el.id)
            artifacts.append(
                DownloadedArtifact(
                    document_type="kvittering_pdf",
                    external_document_id=pdf_el.id,
                    filename=pdf_el.filename or "kvittering-aarsregnskap.pdf",
                    content_type=pdf_el.content_type or "application/pdf",
                    content=data,
                )
            )
        sig_el = instance.element_of_type(DATA_TYPE_SIGNATURE)
        if sig_el is not None:
            data = await self._apps.download_data(instance.ref, token=token, element_id=sig_el.id)
            artifacts.append(
                DownloadedArtifact(
                    document_type="signatur_json",
                    external_document_id=sig_el.id,
                    filename=sig_el.filename or "signatur.json",
                    content_type=sig_el.content_type or "application/json",
                    content=data,
                )
            )
        return tuple(artifacts)
