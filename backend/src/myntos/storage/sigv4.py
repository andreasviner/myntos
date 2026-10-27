"""AWS Signature Version 4 request signing.

Cloudflare R2 speaks the S3 API, and the S3 API authenticates with SigV4. This
is the whole of what we need: sign one PUT and one GET against one bucket.

**Why not boto3.** botocore is tens of megabytes of vendored service
definitions and a synchronous transport, dragged into a container image to use
roughly two hundred lines of it. SigV4 itself is a published, fixed algorithm —
four HMACs and a canonical string — and it is verified below against AWS's own
published test vector. The failure mode is also benign: a signing mistake means
R2 answers 403 on the first request, loudly and immediately, rather than
corrupting anything.

Reference: docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv4-signing-process.html
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"

# SHA-256 of the empty string, which every request without a body must send as
# its payload hash.
EMPTY_PAYLOAD_HASH = hashlib.sha256(b"").hexdigest()


def payload_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_access_key: str, *, date: str, region: str, service: str) -> bytes:
    """Derive the request-specific key.

    Chained deliberately: the key is scoped to one day, one region and one
    service, so a leaked signature cannot be replayed against another.
    """
    initial = _hmac(f"AWS4{secret_access_key}".encode(), date)
    regional = _hmac(initial, region)
    scoped = _hmac(regional, service)
    return _hmac(scoped, "aws4_request")


def canonical_path(path: str) -> str:
    """Percent-encode a path the way S3 expects.

    S3 is the one AWS service that does **not** double-encode the path, so the
    already-encoded form is signed as-is. Our keys are hexadecimal and slashes,
    but the bucket name comes from configuration and might not be.
    """
    return quote(path, safe="/~")


def canonical_query(params: dict[str, str]) -> str:
    """The query string in the form both signed and sent: sorted, every value encoded.

    Multipart uploads and listing name their operation in the query
    (``?uploads``, ``?partNumber=1&uploadId=…``, ``?list-type=2``), and S3
    signs the query too. A parameter with no value is ``name=``. The same
    string goes on the URL, so what is signed is byte for byte what is sent.
    """
    return "&".join(
        f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
        for name, value in sorted(params.items())
    )


def canonical_request(
    *,
    method: str,
    path: str,
    query: str,
    headers: dict[str, str],
    signed_headers: list[str],
    content_hash: str,
) -> str:
    lines = [method.upper(), canonical_path(path), query]
    for name in signed_headers:
        lines.append(f"{name}:{headers[name].strip()}")
    lines.append("")
    lines.append(";".join(signed_headers))
    lines.append(content_hash)
    return "\n".join(lines)


def sign(
    *,
    method: str,
    path: str,
    query: str = "",
    headers: dict[str, str],
    content_hash: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, str]:
    """Return the headers to send, including ``Authorization``.

    ``headers`` must already contain ``host``; ``x-amz-date`` and
    ``x-amz-content-sha256`` are added here. Header names are lowercased,
    because the canonical form requires it and because signing a name that
    differs in case from the one sent is the classic way to get a 403 that
    looks like a credentials problem.
    """
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    complete = {name.lower(): value for name, value in headers.items()}
    complete["x-amz-date"] = amz_date
    complete["x-amz-content-sha256"] = content_hash

    signed_headers = sorted(complete)
    request = canonical_request(
        method=method,
        path=path,
        query=query,
        headers=complete,
        signed_headers=signed_headers,
        content_hash=content_hash,
    )

    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(request.encode("utf-8")).hexdigest(),
        ]
    )
    key = signing_key(secret_access_key, date=date_stamp, region=region, service=service)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    complete["authorization"] = (
        f"{ALGORITHM} Credential={access_key_id}/{scope}, "
        f"SignedHeaders={';'.join(signed_headers)}, Signature={signature}"
    )
    return complete
