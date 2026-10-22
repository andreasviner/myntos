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

The XML itself is not built here: :mod:`myntos.skattemelding` builds the
næringsspesifikasjon from the ledger against the year's own XSDs, and wraps it
with the skattemelding in the envelope. This module carries the bytes and the
lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

from myntos.auth.maskinporten import TokenProvider, get_token_provider
from myntos.core.config import Settings, get_settings
from myntos.integrations.altinn.instances import (
    AppInstanceClient,
    DataElement,
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

# The name Skatteetaten's guide gives the envelope on upload.
ENVELOPE_FILENAME = "skattemelding.xml"

# The process step an instance made with ``skalBekreftesAvRevisor`` waits in
# after the person's confirmation, until the revisor confirms.
REVISOR_TASK = "Task_2Revisor"

# And the opposite for Skatteetaten's own validation endpoint, which answers
# 415 "Content-Type 'text/xml' is not supported" to the Altinn type. Found the
# first time a skattemelding was validated live, in TT02 on 2026-10-21; the
# MVA validation was already sending this.
VALIDATION_CONTENT_TYPE = "application/xml"

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Which copy of the melding is being asked for. ``gjeldende`` (the current one)
# has no path segment of its own; the other two do.
Variant = Literal["gjeldende", "utkast", "fastsatt"]

# What ``klargjoerforhaandsfastsetting`` answers when it has prepared a melding
# (docs/api-v2 in Skatteetaten's repository, "Klargjør part for
# forhåndsfastsetting"). Its other statuses come back as a 400 with the same
# JSON shape, and are returned rather than raised: each one means something
# different to the person (PART_HAR_GJELDENDE, PART_HAR_UGYLDIG_STATUS_I_ER, …).
PREPARED = "OK"


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class SkatteetatenError(Exception):
    """Skatteetaten refused a request, or answered unusably.

    ``status_code`` is the HTTP status when Skatteetaten answered at all, so a
    caller can tell "no melding for this taxpayer yet" (403) from a fault
    without reading the message. ``refusal`` is the ``status`` a JSON refusal
    named (the klargjør calls answer 400 with one).
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, refusal: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.refusal = refusal


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


@dataclass(frozen=True)
class SkattemeldingAnswer:
    """The instance after a submission, and Skatteetaten's answer on it when there is one."""

    instance: Instance
    element: DataElement | None
    content: bytes | None

    @property
    def waits_for_revisor(self) -> bool:
        return self.instance.current_task == REVISOR_TASK

    @property
    def outcome(self) -> tuple[str | None, str | None]:
        """Altinn's substatus, which Skatteetaten sets when it has processed the melding.

        Its label and its description, as Skatteetaten's own test notebook
        reads them (``status.substatus``). Kept as text: the values are
        Skatteetaten's to word, and nothing here branches on them.
        """
        status = self.instance.raw.get("status")
        substatus = status.get("substatus") if isinstance(status, dict) else None
        if not isinstance(substatus, dict):
            return None, None
        label, description = substatus.get("label"), substatus.get("description")
        return (
            label if isinstance(label, str) else None,
            description if isinstance(description, str) else None,
        )


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

    async def prepare_forhaandsfastsetting(
        self, *, inntektsaar: int, identifier: str, id_porten_token: str
    ) -> str:
        """Ask Skatteetaten to prepare a melding for a forhåndsfastsetting, and say what it said.

        A company wound up during the year files that year before the
        ordinary assessment (skatteforvaltningsloven § 8-2), when Skatteetaten
        has no melding for it yet: ``GET`` answers 403 «Skatteplikt ikke
        registrert» until this is called. After ``OK`` the melding appears a
        little later (about 20 seconds in TT02, 2026-10-22), as an utkast that
        carries ``gjelderForhaandsfastsetting``. Calling it again is harmless.

        Returns Skatteetaten's ``status``: ``OK``, or the refusal it gave
        (``PART_HAR_GJELDENDE``, ``FORHAANDSFASTSETTING_ER_IKKE_STOETTET_FOR_INNTEKTSAAR``,
        ``PART_TYPE_ER_IKKE_STOETTET``, ``PART_HAR_UGYLDIG_STATUS_I_ER`` when
        Enhetsregisteret does not mark the company dissolved, ``UKJENT``).
        Only a person's login: the call is part of a filing they make.
        """
        path = f"/api/skattemelding/v2/klargjoerforhaandsfastsetting/{inntektsaar}/{identifier}"
        try:
            response = await self._request(
                "POST",
                path,
                content_type="application/json",
                accept="application/json",
                id_porten_token=id_porten_token,
            )
        except SkatteetatenError as error:
            if error.refusal is not None:
                return error.refusal
            raise
        try:
            answer = response.json()
        except ValueError as error:
            raise SkatteetatenError(f"POST {path}: the answer is not JSON") from error
        status = answer.get("status") if isinstance(answer, dict) else None
        if not isinstance(status, str):
            raise SkatteetatenError(f"POST {path}: the answer has no status")
        return status

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
            content_type=VALIDATION_CONTENT_TYPE,
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
        skal_bekreftes_av_revisor: bool = False,
    ) -> Instance:
        """Create the Altinn instance the melding will be uploaded into.

        A machine token is enough here, and ``inntektsaar`` goes in as a
        ``dataValues`` entry because the app keys the melding on it and it cannot
        be corrected after the instance exists.

        ``skal_bekreftes_av_revisor`` gives the process its revisor step
        (``Task_2Revisor``), and has to be set here for the same reason: the
        step exists only on an instance made with it (docs/revisor in
        Skatteetaten's repository). It must agree with the
        næringsspesifikasjon's own ``skalBekreftesAvRevisor``; an instance with
        the step and a melding without the flag leaves the revisor nothing to
        confirm, and the opposite slips through to be caught in Skatteetaten's
        control. Sent as the string ``"true"``, like every value in Altinn's
        ``dataValues``, and left out when false, which is what an instance
        made without it means.
        """
        data_values = {"inntektsaar": str(inntektsaar)}
        if skal_bekreftes_av_revisor:
            data_values["skalBekreftesAvRevisor"] = "true"
        return await self._app.create_instance(
            token=altinn_token,
            organisation_number=organisation_number,
            person_number=person_number,
            data_values=data_values,
        )

    async def upload_envelope(
        self, ref: InstanceRef, *, altinn_token: str, envelope: bytes
    ) -> None:
        """Upload the base64 envelope holding melding and næringsspesifikasjon.

        One envelope, one data type, ``text/xml``, named ``skattemelding.xml``
        as Skatteetaten's guide names it (the header goes bare, see
        :func:`myntos.integrations.altinn.instances.content_disposition`). An
        envelope whose base64 næringsspesifikasjon exceeds 10 MB has to go
        through Skatteetaten's asynchronous job API instead — that path is not
        built yet, and the caller will see Altinn refuse the upload rather than
        a silent truncation.
        """
        await self._app.upload_data(
            ref,
            token=altinn_token,
            data_type=ENVELOPE_DATA_TYPE,
            content=envelope,
            content_type=XML_CONTENT_TYPE,
            filename=ENVELOPE_FILENAME,
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

    async def confirm(self, ref: InstanceRef, *, id_porten_altinn_token: str) -> Instance:
        """The person's confirmation alone: one ``process/next``, for an instance with a revisor.

        On an instance made with ``skalBekreftesAvRevisor`` the second step is
        the revisor's (``Task_2Revisor``), who confirms in Skatteetaten's own
        viewer, so Myntos takes the instance as far as the person may and no
        further. The token is the person's, as for :meth:`submit`.
        """
        return await self._app.process_next(ref, token=id_porten_altinn_token)

    async def receipt(self, ref: InstanceRef, *, altinn_token: str) -> Instance:
        """Read the instance back to see whether the receipt has arrived.

        Skatteetaten processes a submission after Altinn accepts it, so the
        receipt appears on the instance a while later. Polling is the caller's
        business; what counts as done is :attr:`Instance.process_ended` plus the
        receipt's data element being present.
        """
        return await self._app.get_instance(ref, token=altinn_token)

    async def feedback(self, ref: InstanceRef, *, altinn_token: str) -> SkattemeldingAnswer:
        """Skatteetaten's answer to a submission, and where the instance has got to.

        ``content`` is ``None`` until Skatteetaten has processed the melding:
        the document appears some time after Altinn accepts the submission, so
        a caller asks again later, and the absence is not worth an exception.
        The instance comes back either way, because its process step says
        whether it still waits for the revisor, and Altinn's substatus says, in
        Skatteetaten's words, whether the melding was taken.
        """
        instance = await self._app.get_instance(ref, token=altinn_token)
        element = instance.element_of_type(FEEDBACK_DATA_TYPE)
        content = None
        if element is not None:
            content = await self._app.download_data(ref, token=altinn_token, element_id=element.id)
        return SkattemeldingAnswer(instance=instance, element=element, content=content)

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
        accept: str = "application/xml",
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

        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
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
            # The klargjør calls refuse with a 400 whose JSON names the reason;
            # kept on the error so the caller can return it as an answer.
            refusal = None
            if accept == "application/json" and response.status_code == httpx.codes.BAD_REQUEST:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and isinstance(body.get("status"), str):
                    refusal = body["status"]
            raise SkatteetatenError(
                f"{method} {path} failed ({response.status_code}): {response.text[:400]}",
                status_code=response.status_code,
                refusal=refusal,
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
