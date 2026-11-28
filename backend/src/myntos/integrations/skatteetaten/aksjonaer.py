"""Skatteetaten Aksjonærregisteroppgaven (RF-1086) HTTP client.

Implements Skatteetaten's ``Innrapportering Aksjonærregister 1086 API`` v1.3.0
(OpenAPI 3.0.3):

- ``POST /{inntektsaar}/1086H``: submit Hovedskjema XML, receive ``hovedskjemaId``.
- ``POST /{inntektsaar}/{hovedskjemareferanse}/1086U``: submit one Underskjema XML.
- ``POST /{inntektsaar}/{hovedskjemareferanse}/bekreft?antall_underskjema={n}``:
  confirm that Hovedskjema and all ``n`` Underskjemaer are submitted.
- ``GET /prefill/{inntektsaar}/{organisasjonsnummer}?page={p}``: fetch last year's
  approved filing as XML documents for prefill (404 when none exists).
- ``GET /{inntektsaar}/forsendelser/{forsendelseid}/dokumenter`` and
  ``GET /{inntektsaar}/forsendelser/{forsendelseId}/dokumenter/{dokumentId}``:
  retrieve archived submission documents.

Every endpoint requires a Maskinporten systembruker access token carrying
:data:`SCOPE`. Neither XML payloads nor personal identifiers are ever logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from myntos.core.config import Settings, get_settings

SCOPE = "skatteetaten:innrapporteringaksjonaerregisteroppgave"
_BASE_PATH = "/api/aksjonaerregister/v1"
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_PREFILL_PAGES = 50


def _client() -> httpx.AsyncClient:
    """Seam for tests to replace with a stub transport."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


@dataclass(frozen=True)
class FeilSpesifisering:
    kode: str | None
    melding: str | None
    sti: str | None
    angitt_verdi: str | None


class SkatteetatenAksjonaerError(Exception):
    """Skatteetaten refused or failed an RF-1086 API request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        correlation_id: str | None = None,
        details: tuple[FeilSpesifisering, ...] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.correlation_id = correlation_id
        self.details = details

    def user_message(self) -> str:
        """Safe Norwegian summary suitable for storing on a failed filing row."""
        parts: list[str] = []
        if self.code:
            parts.append(f"[{self.code}]")
        parts.append(self.message)
        if self.details:
            detail_texts = [
                f"{item.sti}: {item.melding}" if item.sti else (item.melding or item.kode or "")
                for item in self.details[:3]
                if item.melding or item.kode
            ]
            if detail_texts:
                parts.append(" (" + "; ".join(detail_texts) + ")")
        return " ".join(parts)[:1000]


class SkatteetatenAksjonaerNotConfiguredError(SkatteetatenAksjonaerError):
    """``SKATTEETATEN_API_BASE_URL`` is not configured on this deployment."""


@dataclass(frozen=True)
class Leveransebekreftelse:
    leveranse_referanse: str | None
    dialog_id: str | None
    forsendelse_id: str | None


@dataclass(frozen=True)
class PrefillDocument:
    aksjeklasse: str | None
    namespace: str | None
    dokument: str


@dataclass(frozen=True)
class PrefillPage:
    total_items: int
    total_pages: int
    current_page: int
    oppgave_status: str | None
    dokumenter: tuple[PrefillDocument, ...]


@dataclass(frozen=True)
class DokumentPage:
    total_items: int
    total_pages: int
    current_page: int
    dokumenter: tuple[str, ...]


def base_url(settings: Settings | None = None) -> str:
    """Resolve Skatteetaten's RF-1086 base URL from ``SKATTEETATEN_API_BASE_URL``."""
    cfg = settings or get_settings()
    raw = (cfg.skatteetaten_api_base_url or "").strip().rstrip("/")
    if not raw:
        raise SkatteetatenAksjonaerNotConfiguredError(
            "Skatteetaten API er ikke konfigurert på denne installasjonen."
        )
    return f"{raw}{_BASE_PATH}"


def _parse_error(response: httpx.Response) -> SkatteetatenAksjonaerError:
    """Parse Skatteetaten's ``Feil`` JSON body into a structured error."""
    code: str | None = None
    message = f"Skatteetaten svarte HTTP {response.status_code}."
    correlation_id: str | None = None
    details: list[FeilSpesifisering] = []

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        raw_code = payload.get("kode")
        raw_msg = payload.get("melding")
        raw_corr = payload.get("korrelasjonsid")
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code.strip()
        if isinstance(raw_msg, str) and raw_msg.strip():
            message = raw_msg.strip()
        if isinstance(raw_corr, str) and raw_corr.strip():
            correlation_id = raw_corr.strip()
        raw_specs = payload.get("spesifisering")
        if isinstance(raw_specs, list):
            for spec in raw_specs:
                if not isinstance(spec, dict):
                    continue
                details.append(
                    FeilSpesifisering(
                        kode=str(spec["kode"]) if spec.get("kode") is not None else None,
                        melding=str(spec["melding"]) if spec.get("melding") is not None else None,
                        sti=str(spec["sti"]) if spec.get("sti") is not None else None,
                        angitt_verdi=(
                            str(spec["angittVerdi"])
                            if spec.get("angittVerdi") is not None
                            else None
                        ),
                    )
                )

    return SkatteetatenAksjonaerError(
        message,
        status_code=response.status_code,
        code=code,
        correlation_id=correlation_id,
        details=tuple(details),
    )


async def submit_hovedskjema(
    token: str,
    *,
    year: int,
    xml: str,
    idempotency_key: str,
    settings: Settings | None = None,
) -> str:
    """POST ``/{year}/1086H`` and return the created ``hovedskjemaId`` UUID."""
    url = f"{base_url(settings)}/{year}/1086H"
    headers = {
        "Authorization": f"Bearer {token}",
        "idempotencyKey": idempotency_key,
        "Content-Type": "application/xml",
        "Accept": "application/json",
    }
    try:
        async with _client() as client:
            response = await client.post(url, content=xml.encode("utf-8"), headers=headers)
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError(
            "Klarte ikke å nå Skatteetatens tjeneste for aksjonærregisteroppgaven."
        ) from err

    if response.status_code >= 400:
        raise _parse_error(response)

    try:
        body = response.json()
    except ValueError as err:
        raise SkatteetatenAksjonaerError(
            "Uventet svar fra Skatteetaten ved innsending av hovedskjema.",
            status_code=response.status_code,
        ) from err

    # OpenAPI schema spells ``hovedskjemaId``; Skatteetaten's prose example uses
    # lowercase ``hovedskjemaid``. Accept both.
    hoved_id = body.get("hovedskjemaId") or body.get("hovedskjemaid")
    if not isinstance(hoved_id, str) or not hoved_id.strip():
        raise SkatteetatenAksjonaerError(
            "Skatteetaten returnerte ingen hovedskjemaId etter innsending av hovedskjema.",
            status_code=response.status_code,
        )
    return hoved_id.strip()


async def submit_underskjema(
    token: str,
    *,
    year: int,
    hovedskjema_id: str,
    xml: str,
    idempotency_key: str,
    settings: Settings | None = None,
) -> None:
    """POST ``/{year}/{hovedskjema_id}/1086U`` for one shareholder."""
    url = f"{base_url(settings)}/{year}/{hovedskjema_id}/1086U"
    headers = {
        "Authorization": f"Bearer {token}",
        "idempotencyKey": idempotency_key,
        "Content-Type": "application/xml",
        "Accept": "application/json",
    }
    try:
        async with _client() as client:
            response = await client.post(url, content=xml.encode("utf-8"), headers=headers)
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError(
            "Klarte ikke å sende underskjema til Skatteetaten."
        ) from err

    if response.status_code >= 400:
        raise _parse_error(response)


async def confirm_submission(
    token: str,
    *,
    year: int,
    hovedskjema_id: str,
    antall_underskjema: int,
    settings: Settings | None = None,
) -> Leveransebekreftelse:
    """POST ``/{year}/{hovedskjema_id}/bekreft?antall_underskjema={n}``."""
    url = f"{base_url(settings)}/{year}/{hovedskjema_id}/bekreft"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with _client() as client:
            response = await client.post(
                url,
                params={"antall_underskjema": antall_underskjema},
                headers=headers,
            )
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError(
            "Klarte ikke å bekrefte innsendingen hos Skatteetaten."
        ) from err

    if response.status_code >= 400:
        raise _parse_error(response)

    try:
        body = response.json()
    except ValueError as err:
        raise SkatteetatenAksjonaerError(
            "Uventet svar fra Skatteetaten ved bekreftelse av innsendingen.",
            status_code=response.status_code,
        ) from err

    return Leveransebekreftelse(
        leveranse_referanse=(
            str(body["oppgavegiversLeveranseReferanse"])
            if body.get("oppgavegiversLeveranseReferanse")
            else None
        ),
        dialog_id=str(body["dialogId"]) if body.get("dialogId") else None,
        forsendelse_id=str(body["forsendelseId"]) if body.get("forsendelseId") else None,
    )


async def fetch_prefill(
    token: str,
    *,
    year: int,
    orgnr: str,
    page: int = 0,
    settings: Settings | None = None,
) -> PrefillPage | None:
    """GET ``/prefill/{year}/{orgnr}?page={page}``, returning ``None`` on 404."""
    url = f"{base_url(settings)}/prefill/{year}/{orgnr}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with _client() as client:
            response = await client.get(url, params={"page": page}, headers=headers)
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError("Klarte ikke å hente prefill fra Skatteetaten.") from err

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise _parse_error(response)

    try:
        body = response.json()
    except ValueError as err:
        raise SkatteetatenAksjonaerError(
            "Uventet svar fra Skatteetaten ved henting av prefill.",
            status_code=response.status_code,
        ) from err

    docs: list[PrefillDocument] = []
    for item in body.get("dokumenter") or []:
        if not isinstance(item, dict):
            continue
        xml_str = item.get("dokument")
        if not isinstance(xml_str, str) or not xml_str.strip():
            continue
        docs.append(
            PrefillDocument(
                aksjeklasse=(
                    str(item["aksjeklasse"]) if item.get("aksjeklasse") is not None else None
                ),
                namespace=str(item["namespace"]) if item.get("namespace") is not None else None,
                dokument=xml_str,
            )
        )

    return PrefillPage(
        total_items=int(body.get("totalItems") or len(docs)),
        total_pages=int(body.get("totalPages") or 1),
        current_page=int(body.get("currentPage") or page),
        oppgave_status=(
            str(body["oppgaveStatus"]) if body.get("oppgaveStatus") is not None else None
        ),
        dokumenter=tuple(docs),
    )


async def fetch_all_prefill(
    token: str,
    *,
    year: int,
    orgnr: str,
    settings: Settings | None = None,
) -> tuple[str | None, list[PrefillDocument]]:
    """Fetch every prefill page for ``year`` and ``orgnr``."""
    first = await fetch_prefill(token, year=year, orgnr=orgnr, page=0, settings=settings)
    if first is None:
        return None, []
    all_docs = list(first.dokumenter)
    total_pages = min(first.total_pages, _MAX_PREFILL_PAGES)
    for page_num in range(1, total_pages):
        next_page = await fetch_prefill(
            token, year=year, orgnr=orgnr, page=page_num, settings=settings
        )
        if next_page is None:
            break
        all_docs.extend(next_page.dokumenter)
    return first.oppgave_status, all_docs


async def fetch_documents(
    token: str,
    *,
    year: int,
    forsendelse_id: str,
    page: int = 0,
    size: int = 50,
    settings: Settings | None = None,
) -> DokumentPage:
    """GET ``/{year}/forsendelser/{forsendelse_id}/dokumenter``."""
    url = f"{base_url(settings)}/{year}/forsendelser/{forsendelse_id}/dokumenter"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with _client() as client:
            response = await client.get(url, params={"page": page, "size": size}, headers=headers)
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError(
            "Klarte ikke å hente arkiverte dokumenter fra Skatteetaten."
        ) from err

    if response.status_code >= 400:
        raise _parse_error(response)

    body = response.json()
    docs = tuple(str(item) for item in (body.get("dokumenter") or []) if isinstance(item, str))
    return DokumentPage(
        total_items=int(body.get("totalItems") or len(docs)),
        total_pages=int(body.get("totalPages") or 1),
        current_page=int(body.get("currentPage") or page),
        dokumenter=docs,
    )


async def fetch_document(
    token: str,
    *,
    year: int,
    forsendelse_id: str,
    dokument_id: str,
    settings: Settings | None = None,
) -> tuple[bytes, str]:
    """GET ``/{year}/forsendelser/{forsendelse_id}/dokumenter/{dokument_id}``."""
    url = f"{base_url(settings)}/{year}/forsendelser/{forsendelse_id}/dokumenter/{dokument_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with _client() as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as err:
        raise SkatteetatenAksjonaerError(
            "Klarte ikke å hente dokumentet fra Skatteetaten."
        ) from err

    if response.status_code >= 400:
        raise _parse_error(response)

    media = (response.headers.get("content-type") or "application/octet-stream").split(";")[0]
    return response.content, media.strip()
