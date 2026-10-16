"""Cloudflare R2 storage for bilag documents (production).

R2 speaks the S3 API, so this is an S3 client narrowed to the two operations a
bilag archive performs: put an object, get an object (and put a thumbnail,
which may land on one already there; see ``save_derived``). There is no delete, here
or anywhere above it — bokføringsloven § 13 requires primary documentation to
stay retrievable for five years after the financial year ends, and a delete
path is how that requirement quietly stops being met.

**Jurisdiction is part of the endpoint.** A bucket created with
``jurisdiction=eu`` is only reachable at ``<account>.eu.r2.cloudflarestorage.com``;
the plain hostname addresses a different, non-jurisdictional namespace. The
jurisdiction is fixed at bucket creation and cannot be changed afterwards, so
pointing at the wrong host does not fail over — it silently writes accounting
records outside the EEA. ``R2_JURISDICTION`` therefore defaults to ``eu`` and
has to be turned *off* deliberately.

Transport is httpx (already a dependency) with hand-rolled SigV4
(:mod:`myntos.storage.sigv4`) rather than boto3 — see that module for why.
"""

from __future__ import annotations

import httpx

from myntos.core.clock import utc_now
from myntos.storage.base import ObjectNotFoundError, StorageError
from myntos.storage.keys import new_key, validate_derived_key, validate_key
from myntos.storage.sigv4 import payload_hash, sign

# R2 ignores the region but SigV4 does not: it is part of the signing scope, and
# Cloudflare's documented value is "auto".
REGION = "auto"
SERVICE = "s3"

# Generous, because a 25 MB bilag on a slow connection is a normal request and
# an upload that times out halfway is worse than one that takes a minute.
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


def endpoint_for(account_id: str, jurisdiction: str | None) -> str:
    """The S3 endpoint for an account, in a jurisdiction if one is set."""
    if jurisdiction is None or jurisdiction == "default":
        return f"https://{account_id}.r2.cloudflarestorage.com"
    return f"https://{account_id}.{jurisdiction}.r2.cloudflarestorage.com"


class R2DocumentStorage:
    """Stores documents as objects in one R2 bucket."""

    def __init__(
        self,
        *,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        endpoint_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._endpoint = httpx.URL(endpoint_url.rstrip("/"))
        # Injectable so the tests can drive a transport instead of the network.
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    @property
    def _host(self) -> str:
        port = self._endpoint.port
        default = {"https": 443, "http": 80}.get(self._endpoint.scheme)
        if port is None or port == default:
            return self._endpoint.host
        return f"{self._endpoint.host}:{port}"

    def _object_path(self, key: str) -> str:
        """Path-style addressing: ``/bucket/key``, which R2 supports."""
        return f"{self._endpoint.path.rstrip('/')}/{self._bucket}/{key}"

    async def _send(
        self, method: str, key: str, *, body: bytes = b"", content_type: str | None = None
    ) -> httpx.Response:
        path = self._object_path(key)
        headers = {"host": self._host}
        if content_type is not None:
            headers["content-type"] = content_type
            headers["content-length"] = str(len(body))

        signed = sign(
            method=method,
            path=path,
            headers=headers,
            content_hash=payload_hash(body),
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            region=REGION,
            service=SERVICE,
            now=utc_now(),
        )
        url = self._endpoint.copy_with(raw_path=path.encode("ascii"))
        try:
            return await self._client.request(method, url, headers=signed, content=body)
        except httpx.HTTPError as error:
            raise StorageError(f"nådde ikke dokumentlageret: {error}") from error

    async def save(self, *, content: bytes, filename: str, content_type: str) -> str:
        # filename stays in the database; it never reaches the object store,
        # where it would be attacker-chosen input deciding a path.
        del filename
        key = new_key(content_type)
        response = await self._send("PUT", key, body=content, content_type=content_type)
        if response.status_code not in (200, 201):
            raise StorageError(f"kunne ikke lagre dokumentet i R2 (HTTP {response.status_code})")
        return key

    async def save_derived(self, key: str, *, content: bytes, content_type: str) -> None:
        response = await self._send(
            "PUT", validate_derived_key(key), body=content, content_type=content_type
        )
        if response.status_code not in (200, 201):
            raise StorageError(f"kunne ikke lagre kopien i R2 (HTTP {response.status_code})")

    async def load(self, key: str) -> bytes:
        response = await self._send("GET", validate_key(key))
        if response.status_code == 404:
            raise ObjectNotFoundError(f"dokumentet {key} finnes ikke i lageret")
        if response.status_code != 200:
            raise StorageError(f"kunne ikke hente dokumentet fra R2 (HTTP {response.status_code})")
        return response.content

    async def delete(self, key: str) -> None:
        # Only the retention purge calls this (DocumentStorage.delete). S3, and
        # R2 with it, answers 204 whether or not the object was there.
        response = await self._send("DELETE", validate_key(key))
        if response.status_code not in (200, 204, 404):
            raise StorageError(f"kunne ikke slette dokumentet i R2 (HTTP {response.status_code})")

    async def aclose(self) -> None:
        await self._client.aclose()
