"""The Altinn 3 app instance API.

Every submission this system will ever make — MVA-melding, skattemelding,
årsregnskap — travels the same five steps through an Altinn 3 app:

1. create an instance, owned by the person or company the filing is for;
2. upload one or more data elements (the melding itself, then attachments);
3. ``PUT process/next`` to end the filling step;
4. ``PUT process/next`` **again** to submit — two calls, not one;
5. read the receipt back off the instance.

Only the app URL and the data-type names differ between them, so the mechanics
live here once and the domain-specific clients in
:mod:`myntos.integrations.skatteetaten` decide *what* is uploaded and *when* a
person has to be the one submitting.

Things this module is deliberate about, each of them a documented sharp edge:

- **``Content-Type: text/xml``**, not ``application/xml``, for Skatteetaten's
  XML data types. Altinn's own documentation says otherwise and Skatteetaten
  explicitly flags that as wrong, so the content type is the caller's to state
  and is never guessed from the bytes.
- **Two ``process/next`` calls.** One completes the filling step, the second
  submits. Stopping after the first leaves an instance that looks finished and
  has been submitted to nobody, which is the kind of thing a user discovers from
  a missed deadline.
- **The instance id is ``{party}/{guid}``**, a pair, and every subsequent URL is
  built from both halves. :class:`InstanceRef` keeps them together so a call
  cannot be made with one and a guess at the other.

Nothing here decides who may submit. The Altinn token passed in carries that:
a machine token can create and fill, and only an ID-porten-derived one can
submit an MVA-melding or a skattemelding (docs/integrations/mva.md). The type
system cannot enforce that; the call sites say which token they are using, and
the domain clients ask for the right one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

# A melding with attachments is a slow upload against a service that is not
# fast; the read timeout has to be generous or a working submission fails on the
# clock. The connect timeout stays short — an unreachable Altinn should say so.
_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Altinn's attachment rules, as documented for the MVA app: 57 files, 25 MB
# each, and duplicate filenames within one instance are rejected. Checked here
# rather than discovered from a 400 halfway through an upload set.
MAX_ATTACHMENTS = 57
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Altinn instance ids are "{instanceOwnerPartyId}/{guid}". Parsed rather than
# split blindly so a surprising id is an error here, not a malformed URL later.
_INSTANCE_ID = re.compile(r"^(?P<party>\d+)/(?P<guid>[0-9a-fA-F-]{36})$")


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class AltinnInstanceError(Exception):
    """An Altinn 3 app rejected a request, or answered unusably."""


@dataclass(frozen=True)
class InstanceRef:
    """Where an instance lives: the owning party and the instance's own id."""

    party_id: int
    instance_guid: str

    @property
    def path(self) -> str:
        return f"{self.party_id}/{self.instance_guid}"

    @classmethod
    def parse(cls, instance_id: str) -> InstanceRef:
        match = _INSTANCE_ID.match(instance_id.strip())
        if match is None:
            raise AltinnInstanceError(f"'{instance_id}' is not an Altinn instance id")
        return cls(party_id=int(match["party"]), instance_guid=match["guid"])


@dataclass(frozen=True)
class DataElement:
    """One uploaded document on an instance."""

    id: str
    data_type: str
    content_type: str | None
    size: int | None
    filename: str | None


@dataclass(frozen=True)
class Instance:
    """An instance as the app describes it, reduced to what we act on."""

    ref: InstanceRef
    data: tuple[DataElement, ...]
    # The current process step ("data", "confirmation", …) and task. Absent once
    # the process has ended, which is how a submitted instance is recognised.
    current_task: str | None
    process_ended: bool
    raw: dict[str, Any]

    def element_of_type(self, data_type: str) -> DataElement | None:
        """The first data element of ``data_type``, if the instance has one."""
        for element in self.data:
            if element.data_type == data_type:
                return element
        return None


class AppInstanceClient:
    """One Altinn 3 app, addressed by its absolute URL.

    ``app_url`` is the whole thing — ``https://skd.apps.tt02.altinn.no/skd/
    formueinntekt-skattemelding-v2`` — because the host and the app id both
    differ between test and production, and splitting them into pieces to
    reassemble invites a mismatched pair.
    """

    def __init__(self, app_url: str) -> None:
        self._base = app_url.rstrip("/")

    @property
    def app_url(self) -> str:
        return self._base

    async def create_instance(
        self,
        *,
        token: str,
        organisation_number: str | None = None,
        person_number: str | None = None,
        data_values: dict[str, str] | None = None,
    ) -> Instance:
        """Create an instance owned by a company or a person.

        Exactly one owner, and which one is not a detail: a personlig
        næringsdrivende files the skattemelding on a **fødselsnummer** even when
        the business has an orgnr, so passing both would let a caller submit a
        correct melding against the wrong taxpayer.

        ``data_values`` carries the app's own metadata — ``inntektsaar`` for the
        skattemelding app, for instance — which some apps require at creation
        and cannot be set afterwards.
        """
        if bool(organisation_number) == bool(person_number):
            raise AltinnInstanceError(
                "an instance is owned by either an organisasjonsnummer or a "
                "fødselsnummer — give exactly one"
            )
        owner: dict[str, str] = (
            {"organisationNumber": organisation_number}
            if organisation_number
            else {"personNumber": str(person_number)}
        )
        body: dict[str, Any] = {"instanceOwner": owner}
        if data_values:
            body["dataValues"] = data_values

        payload = await self._request(
            "POST",
            "/instances",
            token=token,
            json=body,
            expected=(httpx.codes.CREATED, httpx.codes.OK),
        )
        return _instance_from(payload)

    async def upload_data(
        self,
        ref: InstanceRef,
        *,
        token: str,
        data_type: str,
        content: bytes,
        content_type: str,
        filename: str | None = None,
    ) -> DataElement:
        """Upload one data element to an instance.

        ``content_type`` is required rather than inferred — see this module's
        docstring on ``text/xml``.
        """
        # Altinn requires Content-Disposition on every upload, named or not:
        # without it the answer is 400 "The request must include a
        # Content-Disposition header", which reads like a problem with the body.
        # It also reads the filename off this header and rejects duplicates
        # within an instance, so an unnamed upload still needs a stable name.
        headers = {
            "Content-Type": content_type,
            "Content-Disposition": (
                f'attachment; filename="{filename or _default_filename(data_type, content_type)}"'
            ),
        }
        payload = await self._request(
            "POST",
            f"/instances/{ref.path}/data",
            token=token,
            params={"dataType": data_type},
            content=content,
            headers=headers,
            expected=(httpx.codes.CREATED, httpx.codes.OK),
        )
        return _element_from(payload)

    async def replace_data(
        self,
        ref: InstanceRef,
        *,
        token: str,
        element_id: str,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> DataElement:
        """Overwrite a data element the app created along with the instance.

        Some apps create a data element at instantiation and expect it to be
        *filled*, not added: the MVA app makes its ``mvaMeldingInnsending``
        envelope this way, and a second one POSTed beside it is refused, because
        the data type allows exactly one. Skatteetaten's guide says PUT for that
        element and POST for everything else.
        """
        payload = await self._request(
            "PUT",
            f"/instances/{ref.path}/data/{element_id}",
            token=token,
            content=content,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            expected=(httpx.codes.OK, httpx.codes.CREATED),
        )
        return _element_from(payload)

    async def upload_attachments(
        self,
        ref: InstanceRef,
        *,
        token: str,
        data_type: str,
        files: list[tuple[str, bytes, str]],
    ) -> list[DataElement]:
        """Upload several attachments, checking Altinn's limits first.

        The count, the per-file size and the duplicate-filename rule are all
        checked before the first byte is sent: a submission that fails on file
        forty of fifty-seven leaves an instance in a state someone has to go and
        understand, and every one of these is knowable in advance.
        """
        if len(files) > MAX_ATTACHMENTS:
            raise AltinnInstanceError(
                f"{len(files)} attachments — Altinn accepts at most {MAX_ATTACHMENTS}"
            )
        names = [name for name, _, _ in files]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise AltinnInstanceError(
                "Altinn rejects duplicate attachment filenames: " + ", ".join(sorted(duplicates))
            )
        for name, content, _ in files:
            if len(content) > MAX_ATTACHMENT_BYTES:
                raise AltinnInstanceError(
                    f"'{name}' is {len(content)} bytes; Altinn's limit is {MAX_ATTACHMENT_BYTES}"
                )

        uploaded: list[DataElement] = []
        for name, content, content_type in files:
            uploaded.append(
                await self.upload_data(
                    ref,
                    token=token,
                    data_type=data_type,
                    content=content,
                    content_type=content_type,
                    filename=name,
                )
            )
        return uploaded

    async def process_next(self, ref: InstanceRef, *, token: str) -> Instance:
        """Advance the instance one process step.

        Called twice to submit: once to complete filling, once to submit. The
        second call is the one that files anything with the state, which is why
        :meth:`complete_and_submit` exists rather than leaving the count to
        whoever is writing the flow that day.
        """
        payload = await self._request("PUT", f"/instances/{ref.path}/process/next", token=token)
        return _instance_from(payload, fallback=ref)

    async def complete_and_submit(self, ref: InstanceRef, *, token: str) -> Instance:
        """Complete the filling step and submit — the two ``process/next`` calls.

        The token this is called with decides who is submitting, and for an
        MVA-melding or a skattemelding that has to be a person's
        (ID-porten-derived) Altinn token. Nothing here can check that; the
        callers in :mod:`myntos.integrations.skatteetaten` are where the rule
        is stated and kept.
        """
        await self.process_next(ref, token=token)
        return await self.process_next(ref, token=token)

    async def get_instance(self, ref: InstanceRef, *, token: str) -> Instance:
        """Read an instance back, to see its process state and data elements."""
        payload = await self._request("GET", f"/instances/{ref.path}", token=token)
        return _instance_from(payload, fallback=ref)

    async def download_data(self, ref: InstanceRef, *, token: str, element_id: str) -> bytes:
        """Download one data element's bytes — a receipt PDF, a validation result."""
        response = await self._send("GET", f"/instances/{ref.path}/data/{element_id}", token=token)
        if response.status_code != httpx.codes.OK:
            raise AltinnInstanceError(
                f"could not download data element {element_id} ({response.status_code})"
            )
        return response.content

    # --- HTTP ---------------------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        request_headers.update(headers or {})
        async with _client() as http:
            try:
                return await http.request(
                    method,
                    f"{self._base}{path}",
                    params=params,
                    json=json,
                    content=content,
                    headers=request_headers,
                )
            except httpx.HTTPError as error:
                raise AltinnInstanceError(f"could not reach {self._base}") from error

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (httpx.codes.OK,),
    ) -> dict[str, Any]:
        response = await self._send(
            method, path, token=token, params=params, json=json, content=content, headers=headers
        )
        if response.status_code not in expected:
            # Altinn's problem details name the validation rule that failed, and
            # that is the difference between a fixable melding and a mystery.
            raise AltinnInstanceError(
                f"{method} {path} failed ({response.status_code}): {response.text[:400]}"
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise AltinnInstanceError(f"{method} {path} did not answer with JSON") from error
        if not isinstance(payload, dict):
            raise AltinnInstanceError(f"{method} {path} answered with unexpected JSON")
        return payload


# Extensions for the content types these apps actually accept. A data element
# with no filename still needs one, and Altinn shows it to a human in the portal,
# so the data type is a better name than anything generated.
_EXTENSIONS = {
    "text/xml": ".xml",
    "application/xml": ".xml",
    "application/pdf": ".pdf",
    "application/json": ".json",
}


def _default_filename(data_type: str, content_type: str) -> str:
    """A filename for an upload whose caller did not name one."""
    media_type = content_type.split(";")[0].strip().lower()
    return f"{data_type}{_EXTENSIONS.get(media_type, '')}"


def _element_from(payload: dict[str, Any]) -> DataElement:
    element_id = payload.get("id")
    data_type = payload.get("dataType")
    if not isinstance(element_id, str) or not isinstance(data_type, str):
        raise AltinnInstanceError("Altinn described a data element without an id or type")
    size = payload.get("size")
    return DataElement(
        id=element_id,
        data_type=data_type,
        content_type=payload.get("contentType"),
        size=size if isinstance(size, int) else None,
        filename=payload.get("filename"),
    )


def _instance_from(payload: dict[str, Any], *, fallback: InstanceRef | None = None) -> Instance:
    """Read an instance description.

    ``process/next`` answers with the process state rather than the whole
    instance in some app versions, so the reference we already had is used when
    the answer does not repeat it — losing track of the instance id at the
    submit step would be the worst possible moment for it.
    """
    instance_id = payload.get("id")
    if isinstance(instance_id, str) and _INSTANCE_ID.match(instance_id.strip()):
        ref = InstanceRef.parse(instance_id)
    elif fallback is not None:
        ref = fallback
    else:
        raise AltinnInstanceError("Altinn created an instance without a usable id")

    raw_data = payload.get("data")
    elements: list[DataElement] = []
    if isinstance(raw_data, list):
        for entry in raw_data:
            if isinstance(entry, dict):
                elements.append(_element_from(entry))

    process = payload.get("process")
    process = process if isinstance(process, dict) else {}
    current = process.get("currentTask")
    current = current if isinstance(current, dict) else {}
    task = current.get("elementId")

    return Instance(
        ref=ref,
        data=tuple(elements),
        current_task=task if isinstance(task, str) else None,
        # An ended process is how a submitted instance is told from a filled
        # one: the receipt arrives afterwards, and only then.
        process_ended=bool(process.get("ended")),
        raw=payload,
    )
