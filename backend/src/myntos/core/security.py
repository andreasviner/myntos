"""HTTP security hardening.

Baseline security headers applied to every API response. The API serves
financial data, so responses are additionally marked non-cacheable.

And a ceiling on request bodies, applied before anything reads one.
"""

import json
import re
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Room on top of a per-path upload limit for the multipart framing: the
#: boundaries and each part's headers, and the form's few small fields.
_MULTIPART_ROOM = 1024 * 1024

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
    # The API is only ever reached over TLS through Caddy; this keeps a
    # browser from being talked into plain HTTP for it on a hostile network.
    "Strict-Transport-Security": "max-age=31536000",
}


async def security_headers_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


class BodySizeLimitMiddleware:
    """Refuse a body bigger than any route takes, before anything reads it.

    FastAPI reads and parses the whole body before it resolves a single
    dependency, so before it knows who is asking. Without a ceiling, a 50 MB
    JSON array posted by nobody to any route grew the process by 1.3 GB before
    it was answered 401, and an upload was spooled for as long as its sender
    kept sending. One process serves every company, so that was everyone's
    outage, and Løynd's on the same box.

    The limits, each the largest thing its routes take: ``large_uploads``, a
    multipart limit per path for the routes that read the previous program's
    files (first match wins, and each gets :data:`_MULTIPART_ROOM` on top for
    the parts' own headers, so a file of exactly the limit is let through),
    ``upload`` for any other multipart body (a few bilag, or a key's held
    request), and ``body`` for the rest, which is JSON whose biggest is an
    opening balance.

    A path whose limit is in the gigabytes must not let FastAPI read its body
    for it: FastAPI would spool all of it before the route asked who sent it.
    The SAF-T import (2 GB, for Fiken's complete export) reads its own body,
    after the membership check; the person refused there sent nothing that was
    kept. Its 413 says «2 GB» rather than the 201 MB a person was shown on
    2026-10-27 and took for the limit, room included.

    A declared Content-Length is refused outright: the server never hands the
    app more than that. A body without one is counted as it arrives and cut off
    at the limit; the app then sees the client leave, and whatever it answers
    is dropped behind the 413 already sent.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        body: int,
        upload: int,
        large_uploads: tuple[tuple[re.Pattern[str], int], ...],
    ) -> None:
        self.app = app
        self.body = body
        self.upload = upload
        self.large_uploads = large_uploads

    def _limit(self, scope: Scope) -> tuple[int, int]:
        """The most this request may send, and the limit to name in a refusal."""
        content_type = _header(scope, b"content-type") or ""
        if not content_type.lower().startswith("multipart/"):
            return self.body, self.body
        for paths, limit in self.large_uploads:
            if paths.match(scope["path"]):
                return limit + _MULTIPART_ROOM, limit
        return self.upload, self.upload

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit, named = self._limit(scope)
        declared = _header(scope, b"content-length")
        if declared is not None:
            if not declared.isdigit() or int(declared) > limit:
                await _too_large(send, named)
                return
            await self.app(scope, receive, send)
            return

        received = 0
        refused = False
        started = False

        async def counted() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    refused = True
                    if not started:
                        await _too_large(send, named)
                    return {"type": "http.disconnect"}
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if refused:
                return
            started = True
            await send(message)

        await self.app(scope, counted, guarded)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key == name:
            return str(value.decode("latin-1"))
    return None


def _named(limit: int) -> str:
    gigabytes, rest = divmod(limit, 1024**3)
    return f"{gigabytes} GB" if gigabytes and not rest else f"{limit // 1024**2} MB"


async def _too_large(send: Send, limit: int) -> None:
    body = json.dumps(
        {"detail": f"forespørselen er større enn {_named(limit)}", "code": "request_too_large"},
        ensure_ascii=False,
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
