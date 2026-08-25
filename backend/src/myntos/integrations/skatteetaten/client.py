"""Skatteetaten's skattemelding APIs (sluttbrukersystem).

Two API sets, one lifecycle (docs/integrations/skatteetaten.md):

**Skatteetatens-API** — REST over XML, authenticated with a Maskinporten token
on the ``skatteetaten:formueinntekt/skattemelding`` scope. Fetch the taxpayer's
current, draft or assessed skattemelding; validate a draft and get *beregnet
skatt* back with the result.

**Altinn3-API** — the submission itself, through the app
``skd/formueinntekt-skattemelding-v2``: create an instance, upload the base64
envelope, ``process/next`` twice, read the receipt.

The division of labour between machine and person is the part that must not
drift. A systembruker may fetch, validate, create the instance and upload; the
**final submission requires a person logged in with ID-porten**. That is why
:meth:`SkattemeldingClient.submit` takes an ID-porten-derived Altinn token as a
required argument instead of reaching for the machine token it could easily get
on its own — the signature is where the rule is visible, and a caller cannot
satisfy it without having actually put a person in front of the melding.

Two smaller edges, both documented and both easy to get wrong:

- The upload's content type is **``text/xml``**. Altinn's own documentation says
  ``application/xml``; Skatteetaten explicitly flags that as wrong.
- A personlig næringsdrivende files on their **fødselsnummer**, never on the
  business's orgnr — so the identifier is passed through as given and nothing
  here tries to normalise it into "the company number".

The XML itself is not built here. Assembling the næringsspesifikasjon from the
ledger, and the year-versioned XSDs it has to satisfy, belong to the
skattemelding milestone (docs/ROADMAP.md phase 5); this module carries the
bytes and the lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from myntos.auth.maskinporten import TokenProvider, get_token_provider
from myntos.core.config import Settings, get_settings
from myntos.integrations.altinn.instances import (
    AppInstanceClient,
    Instance,
    InstanceRef,
)

# The scope the skattemelding APIs check. Registered on the Maskinporten client
# but inert — the portal shows "Tilgang mangler" — until Skatteetaten grants the
# organization access, in test as well as in production.
SCOPE = "skatteetaten:formueinntekt/skattemelding"

# The Altinn data types of the app. Names, not guesses: an unknown dataType is a
# 400 from Altinn with no hint as to the right one. All three verified against
# the app's own metadata on 2026-08-24, which is the only authority:
#   GET {altinn_skattemelding_app_url}/api/v1/applicationmetadata
ENVELOPE_DATA_TYPE = "skattemeldingOgNaeringsspesifikasjon"
ATTACHMENT_DATA_TYPE = "skattemelding-vedlegg"

# Skatteetaten's answer to a submission - the validation result and, once
# processed, the receipt. Named "tilbakemelding" by the app, which matches
# nothing else in this module and is easy to look for under the wrong name.
FEEDBACK_DATA_TYPE = "tilbakemelding"

# Skatteetaten's flag, not Altinn's. See the module docstring.
XML_CONTENT_TYPE = "text/xml"

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Which copy of the melding is being asked for. ``gjeldende`` (the current one)
# has no path segment of its own; the other two do.
Variant = Literal["gjeldende", "utkast", "fastsatt"]


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class SkatteetatenError(Exception):
    """Skatteetaten refused a request, or answered unusably."""


class SkatteetatenNotConfiguredError(SkatteetatenError):
    """The deployment has no Skatteetaten base URL or Altinn app configured."""


@dataclass(frozen=True)
class ValidationResult:
    """What the validation endpoint answered.

    The XML is kept whole rather than parsed into fields. It carries the
    *beregnet skatt* and the individual findings — including the
    myndighetsfastsatt locks (``KanIkkeOverskriveMyndighetsfastsattVerdi``) that
    have to be shown to the user rather than worked around — and every one of
    those is a per-income-year schema this module has no business pinning down.
    """

    content: bytes
    content_type: str | None


class SkattemeldingClient:
    """The skattemelding lifecycle: fetch, validate, submit, receipt.

    ``token_provider`` is the Maskinporten provider used when a machine is
    acting. ``idporten_base_url`` is the same API on a different host, for when a
    person is: Skatteetaten serves these endpoints from two hosts, one per auth
    plane (``api-test.sits.no`` and ``idporten-api-test.sits.no`` in test), and
    presenting a token to the wrong one fails. Passing a person's token is
    therefore the same call against a different base URL, never the same URL with
    a different header.
    """

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        base_url: str,
        app: AppInstanceClient,
        idporten_base_url: str | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._idporten_base_url = (idporten_base_url or "").rstrip("/") or None
        self._app = app

    # --- Skatteetatens-API --------------------------------------------------------

    async def fetch(
        self,
        *,
        inntektsaar: int,
        identifier: str,
        variant: Variant = "gjeldende",
        id_porten_token: str | None = None,
    ) -> bytes:
        """Fetch a taxpayer's skattemelding for one income year.

        ``identifier`` is a fødselsnummer or an organisasjonsnummer depending on
        the taxpayer group, and is used exactly as given — see the module
        docstring on why nothing normalises it.

        Reading is one of the things a machine may do, so ``id_porten_token`` is
        optional here: pass it when the fetch is part of a flow a person is
        already logged into, and the request goes to the ID-porten host.
        """
        path = f"/api/skattemelding/v2/{inntektsaar}/{identifier}"
        if variant != "gjeldende":
            path = f"/api/skattemelding/v2/{variant}/{inntektsaar}/{identifier}"
        response = await self._request("GET", path, id_porten_token=id_porten_token)
        return response.content

    async def validate(
        self,
        *,
        inntektsaar: int,
        identifier: str,
        payload: bytes,
        id_porten_token: str | None = None,
    ) -> ValidationResult:
        """Validate a draft and read the result, which includes beregnet skatt.

        Always the step before a submission. It is also the only place some
        information appears at all — upersonlig skatteplikt comes back in the
        *valider* response and not in the fetch — so its answer is worth
        keeping, not just checking.
        """
        response = await self._request(
            "POST",
            f"/api/skattemelding/v2/valider/{inntektsaar}/{identifier}",
            content=payload,
            content_type=XML_CONTENT_TYPE,
            id_porten_token=id_porten_token,
        )
        return ValidationResult(
            content=response.content,
            content_type=response.headers.get("content-type"),
        )

    # --- Altinn3-API (submission) --------------------------------------------------

    async def create_instance(
        self,
        *,
        altinn_token: str,
        inntektsaar: int,
        organisation_number: str | None = None,
        person_number: str | None = None,
    ) -> Instance:
        """Create the Altinn instance the melding will be uploaded into.

        A machine token is enough here, and ``inntektsaar`` goes in as a
        ``dataValues`` entry because the app keys the melding on it and it cannot
        be corrected after the instance exists.
        """
        return await self._app.create_instance(
            token=altinn_token,
            organisation_number=organisation_number,
            person_number=person_number,
            data_values={"inntektsaar": str(inntektsaar)},
        )

    async def upload_envelope(
        self, ref: InstanceRef, *, altinn_token: str, envelope: bytes
    ) -> None:
        """Upload the base64 envelope holding melding and næringsspesifikasjon.

        One envelope, one data type, ``text/xml``. An envelope whose base64
        næringsspesifikasjon exceeds 10 MB has to go through Skatteetaten's
        asynchronous job API instead — that path is not built yet, and the
        caller will see Altinn refuse the upload rather than a silent truncation.
        """
        await self._app.upload_data(
            ref,
            token=altinn_token,
            data_type=ENVELOPE_DATA_TYPE,
            content=envelope,
            content_type=XML_CONTENT_TYPE,
        )

    async def submit(self, ref: InstanceRef, *, id_porten_altinn_token: str) -> Instance:
        """Submit the melding — the two ``process/next`` calls.

        The argument is named for what it has to be. Skatteetaten requires an
        ID-porten-logged-in person for the final submission of a skattemelding
        (Altinn resource ``app_skd_formueinntekt-skattemelding-v2``), so passing
        a machine token here is not a shortcut that happens to work — it is a
        submission Altinn will refuse, and the name is the warning.
        """
        return await self._app.complete_and_submit(ref, token=id_porten_altinn_token)

    async def receipt(self, ref: InstanceRef, *, altinn_token: str) -> Instance:
        """Read the instance back to see whether the receipt has arrived.

        Skatteetaten processes a submission after Altinn accepts it, so the
        receipt appears on the instance a while later. Polling is the caller's
        business; what counts as done is :attr:`Instance.process_ended` plus the
        receipt's data element being present.
        """
        return await self._app.get_instance(ref, token=altinn_token)

    async def feedback(self, ref: InstanceRef, *, altinn_token: str) -> bytes | None:
        """Skatteetaten's answer to a submission, or ``None`` if it has not come.

        ``receipt`` says whether the instance has finished; this returns what
        Skatteetaten actually said about it. ``None`` is the ordinary early
        answer rather than a failure - the document appears some time after
        Altinn accepts the submission - so a caller polls, and the absence is
        not worth an exception.
        """
        instance = await self._app.get_instance(ref, token=altinn_token)
        element = instance.element_of_type(FEEDBACK_DATA_TYPE)
        if element is None:
            return None
        return await self._app.download_data(ref, token=altinn_token, element_id=element.id)

    async def upload_attachments(
        self,
        ref: InstanceRef,
        *,
        altinn_token: str,
        files: list[tuple[str, bytes, str]],
    ) -> None:
        """Attach PDFs to the melding, within Altinn's limits.

        Optional, and a machine may do it: the attachments are documentation, not
        the melding. Each entry is ``(filename, content, content_type)``.
        """
        await self._app.upload_attachments(
            ref, token=altinn_token, data_type=ATTACHMENT_DATA_TYPE, files=files
        )

    # --- HTTP ----------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        content_type: str | None = None,
        id_porten_token: str | None = None,
    ) -> httpx.Response:
        """One call, on the host that matches the token it is authenticated with.

        The pairing of plane and host is the point: a Maskinporten token to the
        ID-porten host, or the other way round, is rejected by a service that has
        no reason to explain which of the two mistakes was made.
        """
        if id_porten_token is not None:
            if self._idporten_base_url is None:
                raise SkatteetatenNotConfiguredError(
                    "a person-authenticated call needs "
                    "SKATTEETATEN_IDPORTEN_API_BASE_URL — see "
                    "docs/integrations/skatteetaten.md."
                )
            base_url = self._idporten_base_url
            token = id_porten_token
        else:
            base_url = self._base_url
            token = await self._token_provider.get_access_token((SCOPE,))

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/xml"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        async with _client() as http:
            try:
                response = await http.request(
                    method, f"{base_url}{path}", content=content, headers=headers
                )
            except httpx.HTTPError as error:
                raise SkatteetatenError(f"could not reach {base_url}") from error
        if response.is_error:
            raise SkatteetatenError(
                f"{method} {path} failed ({response.status_code}): {response.text[:400]}"
            )
        return response


def build_client(settings: Settings | None = None) -> SkattemeldingClient:
    """The configured skattemelding client, or a readable error.

    Both halves have to be configured, because the lifecycle needs both: the
    REST base URL to fetch and validate, and the Altinn app URL to submit. A
    deployment with only one of them cannot complete a filing, and finding that
    out at the submit step is finding out too late.
    """
    settings = settings or get_settings()
    base_url = (settings.skatteetaten_api_base_url or "").strip()
    app_url = (settings.altinn_skattemelding_app_url or "").strip()
    if not base_url or not app_url:
        raise SkatteetatenNotConfiguredError(
            "the skattemelding integration needs SKATTEETATEN_API_BASE_URL and "
            "ALTINN_SKATTEMELDING_APP_URL — see docs/integrations/skatteetaten.md."
        )
    return SkattemeldingClient(
        token_provider=get_token_provider(),
        base_url=base_url,
        # Optional: only a flow that fetches or validates with a person logged in
        # needs it, and that flow says so by passing a token.
        idporten_base_url=(settings.skatteetaten_idporten_api_base_url or "").strip() or None,
        app=AppInstanceClient(app_url),
    )
