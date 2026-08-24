"""The MVA-melding submission channel.

The melding itself — a list of ``MvaSpesifikasjonslinje`` grouped from the
ledger's SAF-T VAT codes — is built in the MVA milestone
(docs/integrations/mva.md, ROADMAP phase 4). This module is the channel it
travels: validate, then instance, then the two ``process/next`` calls, then the
three documents that come back and have to be kept.

**Everything here needs a person.** The validation and submission scopes
(``skatteetaten:mvameldingvalidering``, ``skatteetaten:mvameldinginnsending``)
are ID-porten scopes, not Maskinporten ones; Maskinporten is accepted only for
the MVA read and information APIs. So the ID-porten access token is a required
argument on every method, and the Altinn app takes the token exchanged from it —
a raw ID-porten token against the app answers 401, which is the confusing
symptom of a missing exchange rather than a permissions problem.

**Three documents, all of them worth keeping.** Once the process has ended the
instance carries a ``valideringsresultat`` (XML), a ``betalingsinformasjon``
(XML, and it holds the payment KID), and a ``kvittering`` (PDF). They are
retrieved together by :meth:`MvaMeldingClient.feedback` because they belong
together — filed with the period's bilag, they are what documents the filing
afterwards. A submitted melding whose receipt was never fetched is a filing
nobody can show.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from myntos.core.config import Settings, get_settings
from myntos.integrations.altinn.instances import (
    AppInstanceClient,
    Instance,
    InstanceRef,
)

# ID-porten scopes. Ordered from Skatteetaten one at a time; a client that has
# not been granted one cannot even start the authorization that asks for it.
VALIDATION_SCOPE = "skatteetaten:mvameldingvalidering"
SUBMISSION_SCOPE = "skatteetaten:mvameldinginnsending"

# The app's data types. The metadata element goes up first: it names the
# taxpayer, the period, the category and the innsendingstype, and the melding is
# read in the light of it.
METADATA_DATA_TYPE = "mvaMeldingInnsending"
MELDING_DATA_TYPE = "mvamelding"
ATTACHMENT_DATA_TYPE = "mvamelding-vedlegg"

XML_CONTENT_TYPE = "text/xml"

# The three documents to persist with the period's bilag once the process ends.
FEEDBACK_DATA_TYPES = ("valideringsresultat", "betalingsinformasjon", "kvittering")

_VALIDATION_PATH = "/api/mva/grensesnittstoette/mva-melding/valider"

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class MvaError(Exception):
    """The MVA validation API or its Altinn app refused a request."""


class MvaNotConfiguredError(MvaError):
    """The deployment has no MVA validation URL or Altinn app configured."""


@dataclass(frozen=True)
class MvaValidation:
    """The validation API's answer, kept whole.

    Not parsed into a verdict here: validation compares ``grunnlag * sats``
    against ``merverdiavgift`` under its own tolerance rules and reports per-line
    findings, and the user has to see those findings — the melding must be shown
    in full and actively confirmed before anything is submitted.
    """

    content: bytes
    content_type: str | None


@dataclass(frozen=True)
class MvaFeedback:
    """What came back after a submission, by data type."""

    instance: Instance
    documents: dict[str, bytes]

    @property
    def is_complete(self) -> bool:
        """Whether every document a filed melding should have has arrived."""
        return all(name in self.documents for name in FEEDBACK_DATA_TYPES)


class MvaMeldingClient:
    """Validation and submission of the MVA-melding, person present throughout."""

    def __init__(self, *, validation_base_url: str, app: AppInstanceClient) -> None:
        self._validation_base_url = validation_base_url.rstrip("/")
        self._app = app

    async def validate(self, melding: bytes, *, id_porten_token: str) -> MvaValidation:
        """Validate the melding XML. Always the first step, never skipped.

        The validation API takes the ID-porten access token directly — it is
        Skatteetaten's own endpoint rather than an Altinn app, so no exchange is
        involved here.
        """
        headers = {
            "Authorization": f"Bearer {id_porten_token}",
            "Content-Type": XML_CONTENT_TYPE,
            "Accept": "application/xml",
        }
        async with _client() as http:
            try:
                response = await http.post(
                    f"{self._validation_base_url}{_VALIDATION_PATH}",
                    content=melding,
                    headers=headers,
                )
            except httpx.HTTPError as error:
                raise MvaError(
                    f"could not reach the MVA validation API at {self._validation_base_url}"
                ) from error
        if response.is_error:
            raise MvaError(
                f"the MVA validation API refused the melding "
                f"({response.status_code}): {response.text[:400]}"
            )
        return MvaValidation(
            content=response.content,
            content_type=response.headers.get("content-type"),
        )

    async def create_instance(
        self,
        *,
        altinn_token: str,
        organisation_number: str | None = None,
        person_number: str | None = None,
    ) -> Instance:
        """Create the Altinn instance for one period's melding."""
        return await self._app.create_instance(
            token=altinn_token,
            organisation_number=organisation_number,
            person_number=person_number,
        )

    async def upload_melding(
        self,
        ref: InstanceRef,
        *,
        altinn_token: str,
        metadata: bytes,
        melding: bytes,
    ) -> None:
        """Upload the metadata and then the melding, in that order.

        The order is the app's: ``mvaMeldingInnsending`` states who is filing,
        for which period, in which category and as which innsendingstype, and the
        melding is interpreted against it.
        """
        await self._app.upload_data(
            ref,
            token=altinn_token,
            data_type=METADATA_DATA_TYPE,
            content=metadata,
            content_type=XML_CONTENT_TYPE,
        )
        await self._app.upload_data(
            ref,
            token=altinn_token,
            data_type=MELDING_DATA_TYPE,
            content=melding,
            content_type=XML_CONTENT_TYPE,
        )

    async def upload_attachments(
        self,
        ref: InstanceRef,
        *,
        altinn_token: str,
        files: list[tuple[str, bytes, str]],
    ) -> None:
        """Attach documents to the melding, within Altinn's limits."""
        await self._app.upload_attachments(
            ref, token=altinn_token, data_type=ATTACHMENT_DATA_TYPE, files=files
        )

    async def submit(self, ref: InstanceRef, *, id_porten_altinn_token: str) -> Instance:
        """Submit the melding — the two ``process/next`` calls.

        Named for the token it must be given: the Altinn token exchanged from a
        person's ID-porten login. This is the point the user's confirmation
        authorises, and the point after which the melding is filed.
        """
        return await self._app.complete_and_submit(ref, token=id_porten_altinn_token)

    async def feedback(self, ref: InstanceRef, *, altinn_token: str) -> MvaFeedback:
        """Fetch whichever of the three receipt documents have arrived.

        Called until :attr:`MvaFeedback.is_complete`; Skatteetaten processes the
        submission after Altinn has accepted it, so an empty answer right after
        submitting is the normal case and not a failure.
        """
        instance = await self._app.get_instance(ref, token=altinn_token)
        documents: dict[str, bytes] = {}
        for data_type in FEEDBACK_DATA_TYPES:
            element = instance.element_of_type(data_type)
            if element is None:
                continue
            documents[data_type] = await self._app.download_data(
                ref, token=altinn_token, element_id=element.id
            )
        return MvaFeedback(instance=instance, documents=documents)


def build_client(settings: Settings | None = None) -> MvaMeldingClient:
    """The configured MVA client, or a readable error."""
    settings = settings or get_settings()
    validation_url = (settings.skatteetaten_mva_api_base_url or "").strip()
    app_url = (settings.altinn_mva_app_url or "").strip()
    if not validation_url or not app_url:
        raise MvaNotConfiguredError(
            "the MVA integration needs SKATTEETATEN_MVA_API_BASE_URL and "
            "ALTINN_MVA_APP_URL — see docs/integrations/mva.md."
        )
    return MvaMeldingClient(
        validation_base_url=validation_url,
        app=AppInstanceClient(app_url),
    )
