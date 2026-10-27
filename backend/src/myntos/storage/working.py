"""Working copies: uploads a job reads and then deletes, kept apart from the bilag archive.

The previous program's SAF-T files and printouts are uploaded once and read in
the background (:mod:`myntos.saft.jobs`). They are needed for days, not years:
once the books are posted, the import is thrown away, or 14 days have passed,
they go. That is the opposite of the bilag archive (:mod:`myntos.storage.r2`),
which has no delete on purpose, so they get a store of their own: in production
a second R2 bucket (``R2_WORKING_BUCKET``) in the same EU jurisdiction, in
development a directory.

**Why not the database, where they were.** A 200 MB column held well enough. A
person moving from Fiken uploads its complete export, 350 to 600 MB with every
document in it (2026-10-27), and in Postgres that is a value near the 1 GB
ceiling, storage Neon bills together with the restore history it keeps of every
row written, and the whole file twice through the process's memory. R2's
standard class costs $0.015 per GB-month with 10 GB-month free, so a working
copy kept two weeks costs nothing worth counting, and it is written and read in
parts, never whole.

**Never the Infrequent Access class.** It bills 30 days for an object deleted
sooner and charges for every read; a working copy is deleted within 14 days and
read at least twice (the review, then the post).

**What deletes them.** The job that is done with a file deletes it at once.
Everything else (an import left untouched for 14 days, books deleted from
Myntos, a delete that failed) is caught by :func:`myntos.saft.jobs.sweep` once a
day, which deletes every object no row points at. The bucket's lifecycle rule
(expire after 15 days, see ``.env.example``) is the backstop for a sweep that
never ran; it is configuration, so nothing here relies on it.

Keys are ``work/<32 hex>``, generated here: nothing uploaded takes part in
building one, as with the archive's keys (:mod:`myntos.storage.keys`).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import uuid
from collections.abc import AsyncIterable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol
from xml.etree import ElementTree as ET

import httpx

from myntos.core.clock import utc_now
from myntos.storage.base import ObjectNotFoundError, StorageError
from myntos.storage.keys import UnknownStorageKeyError
from myntos.storage.r2 import REGION, SERVICE, TIMEOUT, host_header
from myntos.storage.sigv4 import EMPTY_PAYLOAD_HASH, canonical_query, payload_hash, sign

PREFIX = "work/"
KEY_PATTERN = re.compile(r"^work/[0-9a-f]{32}$")

#: The size of every part of a multipart upload but the last. R2, unlike S3,
#: requires those parts to be the same size, and at least 5 MiB. Sixteen keeps a
#: 2 GB upload to 128 parts while holding no more than that in memory.
PART_SIZE = 16 * 1024 * 1024

#: How much of a download is asked for at a time while it is read.
_READ_CHUNK = 1024 * 1024

#: How much one ranged read of a seekable copy asks R2 for. A ZIP read
#: member by member moves forward almost all the time, so a large range is
#: one request where a small one would be eight; a jump back costs one range.
_RANGE_CHUNK = 8 * 1024 * 1024

_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def new_key() -> str:
    return f"{PREFIX}{uuid.uuid4().hex}"


def validate_key(key: str) -> str:
    """Return ``key`` if this module could have made it, else refuse it."""
    if not KEY_PATTERN.match(key):
        raise UnknownStorageKeyError(f"ikke en nøkkel for en arbeidskopi: {key!r}")
    return key


@dataclass(frozen=True)
class Stored:
    key: str
    #: Bytes in the store, which for a compressed file is not what was uploaded.
    size: int


@dataclass(frozen=True)
class Listed:
    key: str
    modified: datetime


class WorkingStore(Protocol):
    async def save(self, chunks: AsyncIterable[bytes]) -> Stored:
        """Store what ``chunks`` yields under a new key. Nothing is kept if it raises."""
        ...

    def open(self, key: str) -> BinaryIO:
        """A stream of the stored bytes, read from the start. Blocking: for a worker thread."""
        ...

    def open_seekable(self, key: str) -> BinaryIO:
        """The stored bytes as a file that can seek, for a ZIP read from its end. Blocking."""
        ...

    async def load(self, key: str) -> bytes:
        """The whole object, for a reader that needs all of it at once (pypdf)."""
        ...

    async def delete(self, key: str) -> None:
        """Remove it. Nothing being there is not an error: the sweep runs again."""
        ...

    async def listing(self) -> list[Listed]:
        """Every working copy in the store, for the sweep."""
        ...


# --- Local (development) ---------------------------------------------------------------


class LocalWorkingStore:
    """Working copies as files under ``root``. Development and tests only."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        return self._root / validate_key(key)

    async def save(self, chunks: AsyncIterable[bytes]) -> Stored:
        key = new_key()
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        # Written beside and renamed into place, so the sweep never lists half a file.
        partial = path.with_name(f".{path.name}.part")
        size = 0
        try:
            with partial.open("xb") as handle:
                async for chunk in chunks:
                    await asyncio.to_thread(handle.write, chunk)
                    size += len(chunk)
            partial.replace(path)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return Stored(key=key, size=size)

    def open(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as error:
            raise ObjectNotFoundError(f"arbeidskopien {key} finnes ikke") from error

    def open_seekable(self, key: str) -> BinaryIO:
        return self.open(key)

    async def load(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._path(key).read_bytes)
        except FileNotFoundError as error:
            raise ObjectNotFoundError(f"arbeidskopien {key} finnes ikke") from error

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def listing(self) -> list[Listed]:
        folder = self._root / PREFIX.rstrip("/")
        return await asyncio.to_thread(self._list, folder)

    @staticmethod
    def _list(folder: Path) -> list[Listed]:
        if not folder.is_dir():
            return []
        return [
            Listed(
                key=f"{PREFIX}{path.name}",
                modified=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            )
            for path in folder.iterdir()
            if KEY_PATTERN.match(f"{PREFIX}{path.name}")
        ]


# --- R2 (production) -------------------------------------------------------------------


class R2WorkingStore:
    """Working copies as objects in their own R2 bucket.

    Writes go up in :data:`PART_SIZE` parts (S3 multipart upload) as they
    arrive, so a 600 MB file never sits in memory whole. Reads stream the
    object; the SAF-T reader parses as it goes, and an export kept for its
    documents is read in ranges where its ZIP directory points. Signed with the archive's own
    SigV4 (:mod:`myntos.storage.sigv4`), not boto3, for the reasons given there.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
        endpoint_url: str,
        client: httpx.AsyncClient | None = None,
        sync_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._endpoint = httpx.URL(endpoint_url.rstrip("/"))
        # Injectable so the tests can drive a transport instead of the network.
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)
        self._sync_transport = sync_transport

    def _path(self, key: str | None) -> str:
        bucket = f"{self._endpoint.path.rstrip('/')}/{self._bucket}"
        return bucket if key is None else f"{bucket}/{validate_key(key)}"

    def _request(
        self,
        method: str,
        key: str | None,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[httpx.URL, dict[str, str]]:
        path = self._path(key)
        query_string = canonical_query(query or {})
        signed = sign(
            method=method,
            path=path,
            query=query_string,
            headers={"host": host_header(self._endpoint)},
            content_hash=payload_hash(body) if body else EMPTY_PAYLOAD_HASH,
            access_key_id=self._access_key_id,
            secret_access_key=self._secret_access_key,
            region=REGION,
            service=SERVICE,
            now=utc_now(),
        )
        raw = path + (f"?{query_string}" if query_string else "")
        return self._endpoint.copy_with(raw_path=raw.encode("ascii")), signed

    async def _send(
        self,
        method: str,
        key: str | None,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> httpx.Response:
        url, headers = self._request(method, key, query=query, body=body)
        try:
            return await self._client.request(method, url, headers=headers, content=body)
        except httpx.HTTPError as error:
            raise StorageError(f"nådde ikke lageret for arbeidskopier: {error}") from error

    @staticmethod
    def _check(response: httpx.Response, what: str, *ok: int) -> httpx.Response:
        if response.status_code not in ok:
            raise StorageError(f"{what} feilet i R2 (HTTP {response.status_code})")
        return response

    async def save(self, chunks: AsyncIterable[bytes]) -> Stored:
        key = new_key()
        buffer = bytearray()
        size = 0
        upload_id: str | None = None
        parts: list[str] = []
        try:
            async for chunk in chunks:
                buffer += chunk
                size += len(chunk)
                while len(buffer) >= PART_SIZE:
                    if upload_id is None:
                        upload_id = await self._begin(key)
                    parts.append(await self._part(key, upload_id, len(parts) + 1, buffer))
                    del buffer[:PART_SIZE]
            if upload_id is None:
                self._check(await self._send("PUT", key, body=bytes(buffer)), "lagring", 200)
            else:
                if buffer:
                    parts.append(await self._part(key, upload_id, len(parts) + 1, buffer))
                await self._complete(key, upload_id, parts)
        except Exception:
            if upload_id is not None:
                await self._abort(key, upload_id)
            raise
        return Stored(key=key, size=size)

    async def _begin(self, key: str) -> str:
        response = self._check(
            await self._send("POST", key, query={"uploads": ""}), "start av opplasting", 200
        )
        # R2's own answer, not an upload: S314 is about XML from outside.
        upload_id = ET.fromstring(response.content).findtext(f"{_S3}UploadId")  # noqa: S314
        if not upload_id:
            raise StorageError("R2 svarte uten UploadId")
        return upload_id

    async def _part(self, key: str, upload_id: str, number: int, buffer: bytearray) -> str:
        body = bytes(buffer[:PART_SIZE])
        response = self._check(
            await self._send(
                "PUT", key, query={"partNumber": str(number), "uploadId": upload_id}, body=body
            ),
            f"del {number} av opplastingen",
            200,
        )
        etag: str | None = response.headers.get("etag")
        if not etag:
            raise StorageError(f"R2 svarte uten ETag for del {number}")
        return etag

    async def _complete(self, key: str, upload_id: str, etags: list[str]) -> None:
        listed = "".join(
            f"<Part><PartNumber>{number}</PartNumber><ETag>{etag}</ETag></Part>"
            for number, etag in enumerate(etags, start=1)
        )
        body = f"<CompleteMultipartUpload>{listed}</CompleteMultipartUpload>".encode()
        response = self._check(
            await self._send("POST", key, query={"uploadId": upload_id}, body=body),
            "avslutning av opplastingen",
            200,
        )
        # S3 may answer 200 and then fail in the body, after the status line
        # was sent; an upload taken as complete would then be an object that
        # is not there.
        if b"<Error>" in response.content:
            raise StorageError("avslutning av opplastingen feilet i R2")

    async def _abort(self, key: str, upload_id: str) -> None:
        # Best effort: an upload left open costs storage until R2 aborts it
        # itself (seven days by default), and the error being handled matters more.
        with contextlib.suppress(StorageError):
            await self._send("DELETE", key, query={"uploadId": upload_id})

    def open(self, key: str) -> BinaryIO:
        url, headers = self._request("GET", key)
        client = httpx.Client(timeout=TIMEOUT, transport=self._sync_transport)
        try:
            response = client.send(client.build_request("GET", url, headers=headers), stream=True)
        except httpx.HTTPError as error:
            client.close()
            raise StorageError(f"nådde ikke lageret for arbeidskopier: {error}") from error
        if response.status_code != 200:
            response.close()
            client.close()
            if response.status_code == 404:
                raise ObjectNotFoundError(f"arbeidskopien {key} finnes ikke")
            raise StorageError(f"kunne ikke hente arbeidskopien (HTTP {response.status_code})")
        return io.BufferedReader(_Download(response, client), buffer_size=_READ_CHUNK)

    def open_seekable(self, key: str) -> BinaryIO:
        # A ZIP's table of contents is at its end, and its members are read
        # where the table says they start. Downloading 600 MB to a temporary
        # file first would put it in /tmp, which on the production host is
        # memory (PrivateTmp on tmpfs), so it is read in ranges instead.
        client = httpx.Client(timeout=TIMEOUT, transport=self._sync_transport)
        try:
            url, headers = self._request("HEAD", key)
            try:
                response = client.send(client.build_request("HEAD", url, headers=headers))
            except httpx.HTTPError as error:
                raise StorageError(f"nådde ikke lageret for arbeidskopier: {error}") from error
            if response.status_code == 404:
                raise ObjectNotFoundError(f"arbeidskopien {key} finnes ikke")
            self._check(response, "oppslag", 200)
            size = int(response.headers.get("content-length", "-1"))
            if size < 0:
                raise StorageError("R2 svarte uten størrelsen på arbeidskopien")
        except BaseException:
            client.close()
            raise
        ranges = _Ranges(lambda: self._request("GET", key), client, key, size)
        return io.BufferedReader(ranges, buffer_size=_RANGE_CHUNK)

    async def load(self, key: str) -> bytes:
        response = await self._send("GET", key)
        if response.status_code == 404:
            raise ObjectNotFoundError(f"arbeidskopien {key} finnes ikke")
        return self._check(response, "henting", 200).content

    async def delete(self, key: str) -> None:
        # S3, and R2 with it, answers 204 whether or not the object was there.
        self._check(await self._send("DELETE", key), "sletting", 200, 204, 404)

    async def listing(self) -> list[Listed]:
        found: list[Listed] = []
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": PREFIX}
            if token is not None:
                query["continuation-token"] = token
            listed = self._check(await self._send("GET", None, query=query), "listing", 200)
            page = ET.fromstring(listed.content)  # noqa: S314 - R2's own answer, as above
            for item in page.iter(f"{_S3}Contents"):
                key = item.findtext(f"{_S3}Key") or ""
                modified = item.findtext(f"{_S3}LastModified") or ""
                if KEY_PATTERN.match(key) and modified:
                    found.append(
                        Listed(
                            key=key,
                            modified=datetime.fromisoformat(modified.replace("Z", "+00:00")),
                        )
                    )
            token = page.findtext(f"{_S3}NextContinuationToken")
            if page.findtext(f"{_S3}IsTruncated") != "true" or not token:
                return found

    async def aclose(self) -> None:
        await self._client.aclose()


class _Download(io.RawIOBase):
    """An object being downloaded, as a file read from the start; closing it ends the download."""

    def __init__(self, response: httpx.Response, client: httpx.Client) -> None:
        self._response = response
        self._client = client
        # iter_bytes, not iter_raw: the same bytes here, since nothing is stored
        # with a Content-Encoding, and it also serves a body already read.
        self._chunks: Iterator[bytes] = response.iter_bytes(_READ_CHUNK)
        self._pending = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        while not self._pending:
            try:
                self._pending = memoryview(next(self._chunks))
            except StopIteration:
                return 0
            except httpx.HTTPError as error:
                raise StorageError(f"nedlastingen av arbeidskopien brøt: {error}") from error
        count = min(len(buffer), len(self._pending))
        buffer[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        return count

    def close(self) -> None:
        if not self.closed:
            self._response.close()
            self._client.close()
        super().close()


class _Ranges(io.RawIOBase):
    """An object read in byte ranges wherever it is asked to seek; closing it closes the client.

    The ``Range`` header is sent unsigned: SigV4 signs ``host`` and the
    payload, and S3 (R2 with it) takes any other header beside them.
    """

    def __init__(
        self,
        signed: Callable[[], tuple[httpx.URL, dict[str, str]]],
        client: httpx.Client,
        key: str,
        size: int,
    ) -> None:
        self._signed = signed
        self._client = client
        self._key = key
        self._size = size
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._size}[whence]
        if base + offset < 0:
            raise ValueError("kan ikke lese før starten av arbeidskopien")
        self._position = base + offset
        return self._position

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        wanted = min(len(buffer), self._size - self._position)
        if wanted <= 0:
            return 0
        url, headers = self._signed()
        last = self._position + wanted - 1
        headers = {**headers, "range": f"bytes={self._position}-{last}"}
        try:
            response = self._client.send(self._client.build_request("GET", url, headers=headers))
        except httpx.HTTPError as error:
            raise StorageError(f"nedlastingen av arbeidskopien brøt: {error}") from error
        if response.status_code == 404:
            raise ObjectNotFoundError(f"arbeidskopien {self._key} finnes ikke")
        if response.status_code != 206 or len(response.content) != wanted:
            # A 200 is the whole object, which a range must never turn into
            # without saying so; better to stop than to read the wrong bytes.
            raise StorageError(
                f"R2 svarte ikke med bitene det ble bedt om (HTTP {response.status_code})"
            )
        buffer[:wanted] = response.content
        self._position += wanted
        return wanted

    def close(self) -> None:
        if not self.closed:
            self._client.close()
        super().close()
