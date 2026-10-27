"""The store for working copies: uploads a job reads and then deletes.

As in ``test_storage``, R2 is an ``httpx.MockTransport``: what is checked is
ours, that a file goes up in parts R2 accepts (every part but the last the
same size), that a failure leaves no upload open, and that the operation each
request names in its query is the one that was signed.
"""

import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

from myntos.core.config import Settings
from myntos.storage import StorageNotConfiguredError, _r2_working
from myntos.storage import working as working_module
from myntos.storage.base import ObjectNotFoundError, StorageError
from myntos.storage.keys import UnknownStorageKeyError
from myntos.storage.sigv4 import canonical_query
from myntos.storage.working import LocalWorkingStore, R2WorkingStore

KEY = "work/" + "0" * 32


async def _chunks(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


def r2_with(handle: httpx.MockTransport, sync: httpx.MockTransport | None = None) -> R2WorkingStore:
    return R2WorkingStore(
        access_key_id="AKID",
        secret_access_key="secret",
        bucket="myntos-working",
        endpoint_url="https://acct.eu.r2.cloudflarestorage.com",
        client=httpx.AsyncClient(transport=handle),
        sync_transport=sync,
    )


def _query(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.url.query.decode(), keep_blank_values=True))


def test_the_query_is_signed_as_it_is_sent() -> None:
    # Sorted by name, every value encoded, a bare name as ``name=``: what S3
    # signs, and so what has to be on the URL.
    assert canonical_query({"uploadId": "a/b c", "partNumber": "2"}) == (
        "partNumber=2&uploadId=a%2Fb%20c"
    )
    assert canonical_query({"uploads": ""}) == "uploads="


async def test_a_small_file_goes_up_in_one_request() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    stored = await r2_with(httpx.MockTransport(handle)).save(_chunks(b"abc", b"def"))
    assert stored.size == 6
    assert [(r.method, r.url.path) for r in seen] == [("PUT", f"/myntos-working/{stored.key}")]
    assert seen[0].content == b"abcdef"
    assert seen[0].headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")


async def test_a_large_file_goes_up_in_parts_of_one_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(working_module, "PART_SIZE", 8)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        query = _query(request)
        if request.method == "POST" and "uploads" in query:
            return httpx.Response(
                200,
                content=b'<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/'
                b'2006-03-01/"><UploadId>U1</UploadId></InitiateMultipartUploadResult>',
            )
        if request.method == "PUT":
            return httpx.Response(200, headers={"etag": f'"e{query["partNumber"]}"'})
        return httpx.Response(200, content=b"<CompleteMultipartUploadResult/>")

    stored = await r2_with(httpx.MockTransport(handle)).save(_chunks(*[b"12345"] * 4))
    assert stored.size == 20
    parts = [r for r in seen if r.method == "PUT"]
    # R2 refuses parts of different sizes, the last one aside.
    assert [len(r.content) for r in parts] == [8, 8, 4]
    assert [_query(r)["partNumber"] for r in parts] == ["1", "2", "3"]
    assert all(_query(r)["uploadId"] == "U1" for r in parts)
    complete = seen[-1]
    assert complete.method == "POST"
    assert _query(complete) == {"uploadId": "U1"}
    assert complete.content.count(b"<Part>") == 3
    assert b'<ETag>"e3"</ETag>' in complete.content
    # Signed with the query it was sent with.
    assert complete.url.query.decode() == canonical_query({"uploadId": "U1"})


@pytest.mark.parametrize("fails", ["part", "completion"])
async def test_a_failed_upload_is_aborted(monkeypatch: pytest.MonkeyPatch, fails: str) -> None:
    monkeypatch.setattr(working_module, "PART_SIZE", 8)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        query = _query(request)
        if request.method == "POST" and "uploads" in query:
            return httpx.Response(
                200,
                content=b'<R xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b"<UploadId>U1</UploadId></R>",
            )
        if request.method == "PUT":
            if fails == "part" and query["partNumber"] == "2":
                return httpx.Response(500)
            return httpx.Response(200, headers={"etag": '"e"'})
        if request.method == "POST":
            # S3 may fail a completion in the body of a 200.
            return httpx.Response(200, content=b"<Error><Code>InternalError</Code></Error>")
        return httpx.Response(204)

    with pytest.raises(StorageError):
        await r2_with(httpx.MockTransport(handle)).save(_chunks(b"x" * 20))
    abort = seen[-1]
    assert abort.method == "DELETE"
    assert _query(abort) == {"uploadId": "U1"}


async def test_an_object_is_read_as_it_downloads() -> None:
    content = b"<AuditFile/>" * 10_000

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("0" * 32):
            return httpx.Response(200, content=content)
        return httpx.Response(404)

    store = r2_with(httpx.MockTransport(handle), httpx.MockTransport(handle))
    with store.open(KEY) as stream:
        assert stream.read(12) == b"<AuditFile/>"
        assert stream.read() == content[12:]
    with pytest.raises(ObjectNotFoundError):
        store.open("work/" + "1" * 32)


async def test_a_zip_is_read_in_ranges_where_its_directory_points() -> None:
    """Fiken's export is read member by member from R2, never downloaded whole."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Bilag/kvittering.pdf", b"%PDF-1.7 " + b"k" * 50_000)
        archive.writestr("SAF-T/2025.xml", b"<AuditFile/>")
    content = buffer.getvalue()
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("0" * 32):
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(content))})
        wanted = request.headers["range"].removeprefix("bytes=")
        asked.append(wanted)
        first, last = (int(part) for part in wanted.split("-"))
        return httpx.Response(206, content=content[first : last + 1])

    store = r2_with(httpx.MockTransport(handle), httpx.MockTransport(handle))
    with store.open_seekable(KEY) as raw, zipfile.ZipFile(raw) as archive:
        assert archive.read("SAF-T/2025.xml") == b"<AuditFile/>"
        assert archive.read("Bilag/kvittering.pdf").startswith(b"%PDF-1.7 kkk")
    assert asked, "every byte came through a range"
    with pytest.raises(ObjectNotFoundError):
        store.open_seekable("work/" + "1" * 32)


async def test_a_range_answered_with_the_whole_object_is_refused() -> None:
    """A 200 to a range is not the bytes asked for, and reading on would read the wrong ones."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": "10"})
        return httpx.Response(200, content=b"0123456789")

    store = r2_with(httpx.MockTransport(handle), httpx.MockTransport(handle))
    with store.open_seekable(KEY) as raw:
        raw.seek(4)
        with pytest.raises(StorageError):
            raw.read(2)


async def test_the_listing_follows_every_page() -> None:
    pages = {
        "": b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<IsTruncated>true</IsTruncated><NextContinuationToken>t2</NextContinuationToken>"
        b"<Contents><Key>work/" + b"a" * 32 + b"</Key>"
        b"<LastModified>2026-10-27T10:00:00.000Z</LastModified></Contents></ListBucketResult>",
        "t2": b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        b"<IsTruncated>false</IsTruncated>"
        b"<Contents><Key>work/" + b"b" * 32 + b"</Key>"
        b"<LastModified>2026-10-20T10:00:00.000Z</LastModified></Contents>"
        b"<Contents><Key>something-else</Key>"
        b"<LastModified>2026-10-20T10:00:00.000Z</LastModified></Contents></ListBucketResult>",
    }
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        query = _query(request)
        seen.append(query)
        assert request.url.path == "/myntos-working"
        return httpx.Response(200, content=pages[query.get("continuation-token", "")])

    listed = await r2_with(httpx.MockTransport(handle)).listing()
    assert [item.key for item in listed] == ["work/" + "a" * 32, "work/" + "b" * 32]
    assert listed[1].modified == datetime(2026, 10, 20, 10, tzinfo=UTC)
    assert all(query["prefix"] == "work/" for query in seen)


async def test_a_key_not_made_here_is_refused() -> None:
    store = r2_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    with pytest.raises(UnknownStorageKeyError):
        await store.delete("2026/" + "0" * 32 + ".pdf")


async def test_the_local_store_keeps_and_lets_go(tmp_path: Path) -> None:
    store = LocalWorkingStore(tmp_path)
    stored = await store.save(_chunks(b"<Audit", b"File/>"))
    assert stored.size == 12
    assert await store.load(stored.key) == b"<AuditFile/>"
    with store.open(stored.key) as stream:
        assert stream.read() == b"<AuditFile/>"
    assert [item.key for item in await store.listing()] == [stored.key]
    await store.delete(stored.key)
    assert await store.listing() == []
    with pytest.raises(ObjectNotFoundError):
        await store.load(stored.key)


async def test_a_failed_save_keeps_nothing_locally(tmp_path: Path) -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield b"half"
        raise RuntimeError("the client left")

    store = LocalWorkingStore(tmp_path)
    with pytest.raises(RuntimeError):
        await store.save(broken())
    assert await store.listing() == []
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_the_working_bucket_is_never_the_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "STORAGE_BACKEND": "r2",
        "R2_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "AKID",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET": "myntos-bilag",
        "R2_WORKING_BUCKET": "myntos-bilag",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(StorageNotConfiguredError):
        _r2_working(Settings(_env_file=None))
    monkeypatch.setenv("R2_WORKING_BUCKET", "")
    with pytest.raises(StorageNotConfiguredError) as missing:
        _r2_working(Settings(_env_file=None))
    assert "R2_WORKING_BUCKET" in str(missing.value)
