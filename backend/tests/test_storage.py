"""Storage backends: key discipline, SigV4 correctness, and the R2 round trip.

The R2 tests drive an ``httpx.MockTransport`` rather than the network. What
they check is the part that is ours: the URL, the headers, and that a document
comes back byte-identical. Whether Cloudflare honours a correct SigV4 signature
is not something a unit test can or should establish — but whether we produce
one is, and the vector below settles it.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from myntos.core.config import Settings
from myntos.storage import StorageNotConfiguredError, _r2_storage
from myntos.storage.base import ObjectNotFoundError, StorageError
from myntos.storage.keys import KEY_PATTERN, UnknownStorageKeyError, new_key, validate_key
from myntos.storage.local import LocalDocumentStorage
from myntos.storage.r2 import R2DocumentStorage, endpoint_for
from myntos.storage.sigv4 import EMPTY_PAYLOAD_HASH, canonical_request, sign, signing_key

PDF = b"%PDF-1.7\nhei\n%%EOF\n"

# AWS's own SigV4 test suite, case "get-vanilla". Reproducing it end to end is
# what makes the hand-written signer trustworthy enough to keep boto3 out of
# the image.
VECTOR_SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
VECTOR_SIGNATURE = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


# --- Keys -----------------------------------------------------------------------


def test_new_key_is_opaque_and_year_bucketed() -> None:
    key = new_key("application/pdf")
    assert KEY_PATTERN.match(key), key
    assert key.endswith(".pdf")
    assert key.startswith(f"{datetime.now(UTC).year}/")


def test_an_unknown_content_type_is_not_guessed_at() -> None:
    assert new_key("application/x-mystery").endswith(".bin")


def test_two_keys_never_collide() -> None:
    assert len({new_key("application/pdf") for _ in range(200)}) == 200


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "2026/../../../etc/passwd.pdf",
        "2026/not-hex.pdf",
        "/2026/00000000000000000000000000000000.pdf",
        "2026/00000000000000000000000000000000.pdf/extra",
    ],
)
def test_a_key_we_did_not_issue_is_rejected(key: str) -> None:
    with pytest.raises(UnknownStorageKeyError):
        validate_key(key)


# --- Local backend ---------------------------------------------------------------


async def test_local_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    key = await storage.save(content=PDF, filename="kvittering.pdf", content_type="application/pdf")
    assert await storage.load(key) == PDF


async def test_local_storage_does_not_write_the_filename_to_disk(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    await storage.save(content=PDF, filename="../../evil.pdf", content_type="application/pdf")
    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(written) == 1
    assert "evil" not in str(written[0])


async def test_local_storage_reports_a_missing_object(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    with pytest.raises(ObjectNotFoundError):
        await storage.load("2026/00000000000000000000000000000000.pdf")


# --- SigV4 -----------------------------------------------------------------------


def test_signature_matches_the_published_aws_vector() -> None:
    headers = {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"}
    request = canonical_request(
        method="GET",
        path="/",
        query="",
        headers=headers,
        signed_headers=["host", "x-amz-date"],
        content_hash=EMPTY_PAYLOAD_HASH,
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            "20150830T123600Z",
            "20150830/us-east-1/service/aws4_request",
            hashlib.sha256(request.encode()).hexdigest(),
        ]
    )
    key = signing_key(VECTOR_SECRET, date="20150830", region="us-east-1", service="service")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    assert signature == VECTOR_SIGNATURE


def test_signing_covers_the_payload_and_the_date() -> None:
    """Both must be inside the signature, or a request can be replayed or altered."""
    signed = sign(
        method="PUT",
        path="/bucket/2026/abc.pdf",
        headers={"host": "example.r2.cloudflarestorage.com"},
        content_hash="deadbeef",
        access_key_id="AKID",
        secret_access_key="secret",
        region="auto",
        service="s3",
        now=datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC),
    )
    assert signed["x-amz-date"] == "20260314T120000Z"
    assert signed["x-amz-content-sha256"] == "deadbeef"
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in signed["authorization"]
    assert "Credential=AKID/20260314/auto/s3/aws4_request" in signed["authorization"]


def test_a_different_body_produces_a_different_signature() -> None:
    def signature_for(content_hash: str) -> str:
        return sign(
            method="PUT",
            path="/bucket/key",
            headers={"host": "example.com"},
            content_hash=content_hash,
            access_key_id="AKID",
            secret_access_key="secret",
            region="auto",
            service="s3",
            now=datetime(2026, 3, 14, tzinfo=UTC),
        )["authorization"]

    assert signature_for("a" * 64) != signature_for("b" * 64)


# --- R2 backend ------------------------------------------------------------------


def test_the_jurisdiction_is_part_of_the_endpoint() -> None:
    """A bucket created with jurisdiction=eu is unreachable at the plain host."""
    assert endpoint_for("abc123", "eu") == "https://abc123.eu.r2.cloudflarestorage.com"
    assert endpoint_for("abc123", "default") == "https://abc123.r2.cloudflarestorage.com"
    assert endpoint_for("abc123", None) == "https://abc123.r2.cloudflarestorage.com"


def r2_with(handler: httpx.MockTransport) -> R2DocumentStorage:
    return R2DocumentStorage(
        access_key_id="AKID",
        secret_access_key="secret",
        bucket="myntos-bilag",
        endpoint_url="https://acct.eu.r2.cloudflarestorage.com",
        client=httpx.AsyncClient(transport=handler),
    )


async def test_r2_round_trip() -> None:
    objects: dict[str, bytes] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            objects[request.url.path] = request.content
            return httpx.Response(200)
        stored = objects.get(request.url.path)
        if stored is None:
            return httpx.Response(404)
        return httpx.Response(200, content=stored)

    storage = r2_with(httpx.MockTransport(handle))
    key = await storage.save(content=PDF, filename="bilag.pdf", content_type="application/pdf")
    assert await storage.load(key) == PDF
    assert list(objects) == [f"/myntos-bilag/{key}"]


async def test_r2_signs_every_request() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=PDF)

    storage = r2_with(httpx.MockTransport(handle))
    await storage.save(content=PDF, filename="bilag.pdf", content_type="application/pdf")

    request = seen[0]
    assert request.method == "PUT"
    assert request.headers["host"] == "acct.eu.r2.cloudflarestorage.com"
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    # The hash of the actual body, not of the empty string.
    assert request.headers["x-amz-content-sha256"] == hashlib.sha256(PDF).hexdigest()


async def test_r2_reports_a_missing_object_distinctly() -> None:
    """A 404 means the row and the bucket disagree — worth its own error."""
    storage = r2_with(httpx.MockTransport(lambda _: httpx.Response(404)))
    with pytest.raises(ObjectNotFoundError):
        await storage.load("2026/00000000000000000000000000000000.pdf")


async def test_r2_refuses_a_key_it_could_not_have_issued() -> None:
    storage = r2_with(httpx.MockTransport(lambda _: httpx.Response(200)))
    with pytest.raises(UnknownStorageKeyError):
        await storage.load("../../secrets")


async def test_r2_turns_a_rejected_upload_into_a_storage_error() -> None:
    storage = r2_with(httpx.MockTransport(lambda _: httpx.Response(403)))
    with pytest.raises(StorageError):
        await storage.save(content=PDF, filename="b.pdf", content_type="application/pdf")


async def test_r2_turns_a_transport_failure_into_a_storage_error() -> None:
    def explode(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    storage = r2_with(httpx.MockTransport(explode))
    with pytest.raises(StorageError):
        await storage.save(content=PDF, filename="b.pdf", content_type="application/pdf")


# --- Configuration ---------------------------------------------------------------


R2_VARIABLES = (
    "STORAGE_BACKEND",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_JURISDICTION",
    "R2_ENDPOINT_URL",
)


def isolated_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """Settings built from these environment variables and nothing else.

    Neither the developer's environment nor the repo-root ``.env`` may decide
    the outcome of a configuration test. Set through the environment rather
    than as constructor arguments, because that is the path a deployment takes
    — including the string-to-type coercion.
    """
    for name in R2_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_half_configured_r2_fails_at_startup_not_at_the_first_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = isolated_settings(monkeypatch, STORAGE_BACKEND="r2", R2_ACCOUNT_ID="acct")
    with pytest.raises(StorageNotConfiguredError) as raised:
        _r2_storage(settings)
    assert "R2_ACCESS_KEY_ID" in str(raised.value)
    assert "R2_BUCKET" in str(raised.value)


def test_a_blank_credential_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` ships `R2_BUCKET=`; an empty value must not pass for set."""
    settings = isolated_settings(
        monkeypatch,
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="   ",
        R2_SECRET_ACCESS_KEY="",
        R2_BUCKET="",
    )
    with pytest.raises(StorageNotConfiguredError):
        _r2_storage(settings)


def test_r2_endpoint_defaults_to_the_eu_jurisdiction(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = isolated_settings(
        monkeypatch,
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="AKID",
        R2_SECRET_ACCESS_KEY="secret",
        R2_BUCKET="bilag",
    )
    assert _r2_storage(settings)._host == "acct.eu.r2.cloudflarestorage.com"


def test_leaving_the_jurisdiction_takes_a_deliberate_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = isolated_settings(
        monkeypatch,
        STORAGE_BACKEND="r2",
        R2_ACCOUNT_ID="acct",
        R2_ACCESS_KEY_ID="AKID",
        R2_SECRET_ACCESS_KEY="secret",
        R2_BUCKET="bilag",
        R2_JURISDICTION="default",
    )
    assert _r2_storage(settings)._host == "acct.r2.cloudflarestorage.com"


def test_an_explicit_endpoint_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = isolated_settings(
        monkeypatch,
        STORAGE_BACKEND="r2",
        R2_ACCESS_KEY_ID="AKID",
        R2_SECRET_ACCESS_KEY="secret",
        R2_BUCKET="bilag",
        R2_ENDPOINT_URL="http://localhost:9000",
    )
    assert _r2_storage(settings)._host == "localhost:9000"
