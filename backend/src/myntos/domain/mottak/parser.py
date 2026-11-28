"""Parse raw RFC 5322 inbound emails and extract validated bilag attachments.

Security properties:
- Never executes or stores HTML; only plain-text excerpts (<= 1000 chars) are kept.
- Reads ``Authentication-Results`` from the **outermost** (top-most) header first
  so an attacker cannot spoof a passing SPF/DKIM/DMARC result inside an earlier
  header.
- Validates every attachment through :func:`myntos.domain.bilag.service.validate_document`
  (magic-byte inspection, <= 25 MB, PDF/PNG/JPEG/WebP/GIF/HEIC only). Unsupported
  attachments (.exe, .zip, .html, .svg, etc.) are skipped and counted.
- Skips inline HTML signature logos (``Content-Disposition: inline`` carrying a
  ``Content-ID``) when the email also carries explicit invoice attachments.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import re
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr

from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.errors import DocumentTooLargeError, UnsupportedDocumentError
from myntos.domain.mottak.errors import InvalidInboundEmailError

# Cloudflare Email Routing caps raw messages at 25 MiB; give 1 MiB framing room.
MAX_RAW_EMAIL_BYTES = 26 * 1024 * 1024
MAX_ATTACHMENTS_PER_EMAIL = 10
MAX_BODY_EXCERPT_CHARS = 1000
_INLINE_TRACKING_PIXEL_MAX_BYTES = 4096

_WHITESPACE_RUN = re.compile(r"[ \t\r\n]+")
_AUTH_TOKEN = re.compile(
    r"\b(spf|dkim|dmarc)\s*=\s*(pass|fail|softfail|neutral|none|permerror|temperror|bestguesspass)\b",
    re.IGNORECASE,
)
_RECEIVED_SPF = re.compile(
    r"^\s*(pass|fail|softfail|neutral|none|permerror|temperror)\b",
    re.IGNORECASE,
)
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9æøåÆØÅ._ -]+")
_EXT_BY_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
}


@dataclass(frozen=True)
class ParsedAttachment:
    index: int
    filename: str
    content_type: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    sender_email: str
    sender_name: str | None
    envelope_from: str
    reply_to: str | None
    subject: str
    body_excerpt: str | None
    spf_result: str | None
    dkim_result: str | None
    dmarc_result: str | None
    attachments: tuple[ParsedAttachment, ...]
    skipped_attachments: int


def _clean_single_line(value: str | None, *, limit: int) -> str:
    if not value:
        return ""
    return _WHITESPACE_RUN.sub(" ", str(value)).strip()[:limit]


def _extract_address(header_value: str | None) -> tuple[str | None, str]:
    if not header_value:
        return None, ""
    raw_name, raw_addr = parseaddr(str(header_value))
    name = _clean_single_line(raw_name, limit=255) or None
    addr = raw_addr.strip().lower()[:320]
    return name, addr


def _parse_auth_results(msg: Message) -> tuple[str | None, str | None, str | None]:
    """Read SPF/DKIM/DMARC from the top-most Authentication-Results headers."""
    found: dict[str, str] = {}
    for header in msg.get_all("Authentication-Results", ()):
        text = str(header)
        for match in _AUTH_TOKEN.finditer(text):
            mechanism = match.group(1).lower()
            result = match.group(2).lower()
            if result == "bestguesspass":
                result = "pass"
            # First (outermost MTA) result wins; inner spoofed headers are ignored.
            found.setdefault(mechanism, result)
        if len(found) == 3:
            break

    if "spf" not in found:
        for header in msg.get_all("Received-SPF", ()):
            spf_match = _RECEIVED_SPF.match(str(header))
            if spf_match is not None:
                found["spf"] = spf_match.group(1).lower()
                break

    return found.get("spf"), found.get("dkim"), found.get("dmarc")


def _extract_body_excerpt(msg: Message) -> str | None:
    """Return up to 1000 chars of the first plain-text body part, never HTML."""
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_type().lower() != "text/plain":
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        lines = [line.strip() for line in decoded.splitlines()]
        compact = "\n".join(line for line in lines if line).strip()
        if compact:
            return compact[:MAX_BODY_EXCERPT_CHARS]
    return None


def _safe_filename(raw_name: str | None, *, index: int, content_type: str) -> str:
    ext = _EXT_BY_TYPE.get(content_type, "bin")
    if raw_name:
        # Strip any path components before sanitising characters.
        leaf = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = _SAFE_FILENAME.sub("-", leaf).strip(" .-")
        if cleaned:
            return cleaned[:255]
    return f"vedlegg-{index + 1}.{ext}"


@dataclass(frozen=True)
class _CandidatePart:
    raw_filename: str | None
    payload: bytes
    is_inline_cid: bool


def _collect_candidate_parts(msg: Message) -> list[_CandidatePart]:
    candidates: list[_CandidatePart] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        # Skip plain body parts unless explicitly attached.
        if ctype in ("text/plain", "text/html") and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        is_inline_cid = disposition != "attachment" and bool(part.get("Content-ID"))
        candidates.append(
            _CandidatePart(
                raw_filename=filename,
                payload=payload,
                is_inline_cid=is_inline_cid,
            )
        )

    # Real-world invoice emails frequently include HTML signature logos with
    # Content-ID alongside the real PDF attachment. Ignore Content-ID inline
    # decorations whenever explicit attachments exist; if the email only has
    # inline images (e.g. a phone photo of a receipt), keep images above 4 KiB.
    has_explicit = any(not part.is_inline_cid for part in candidates)
    if has_explicit:
        return [part for part in candidates if not part.is_inline_cid]
    return [
        part
        for part in candidates
        if not (part.is_inline_cid and len(part.payload) < _INLINE_TRACKING_PIXEL_MAX_BYTES)
    ]


def parse_inbound_email(raw_mime: bytes, *, envelope_from: str) -> ParsedEmail:
    """Parse ``raw_mime`` bytes into metadata, auth headers and validated attachments."""
    if not raw_mime:
        raise InvalidInboundEmailError("e-postinnholdet er tomt")
    if len(raw_mime) > MAX_RAW_EMAIL_BYTES:
        raise InvalidInboundEmailError("e-posten overskrider maksgrensen på 26 MB")

    msg = email.message_from_bytes(raw_mime, policy=email.policy.default)

    _, env_addr = _extract_address(envelope_from)
    sender_name, header_from = _extract_address(msg.get("From"))
    sender_email = header_from or env_addr
    if not sender_email:
        raise InvalidInboundEmailError("e-posten mangler avsenderadresse")

    _, reply_to_addr = _extract_address(msg.get("Reply-To"))
    reply_to = reply_to_addr or None

    raw_msg_id = _clean_single_line(msg.get("Message-ID"), limit=255)
    if raw_msg_id:
        message_id = raw_msg_id
    else:
        digest = hashlib.sha256(raw_mime).hexdigest()
        message_id = f"<sha256:{digest}@myntos.local>"

    subject = _clean_single_line(msg.get("Subject"), limit=500)
    body_excerpt = _extract_body_excerpt(msg)
    spf, dkim, dmarc = _parse_auth_results(msg)

    attachments: list[ParsedAttachment] = []
    skipped = 0
    for candidate in _collect_candidate_parts(msg):
        if len(attachments) >= MAX_ATTACHMENTS_PER_EMAIL:
            skipped += 1
            continue
        try:
            detected_type = bilag_service.validate_document(candidate.payload)
        except (UnsupportedDocumentError, DocumentTooLargeError):
            skipped += 1
            continue
        idx = len(attachments)
        attachments.append(
            ParsedAttachment(
                index=idx,
                filename=_safe_filename(
                    candidate.raw_filename, index=idx, content_type=detected_type
                ),
                content_type=detected_type,
                content=candidate.payload,
                sha256=hashlib.sha256(candidate.payload).hexdigest(),
            )
        )

    return ParsedEmail(
        message_id=message_id,
        sender_email=sender_email,
        sender_name=sender_name,
        envelope_from=env_addr or sender_email,
        reply_to=reply_to,
        subject=subject,
        body_excerpt=body_excerpt,
        spf_result=spf,
        dkim_result=dkim,
        dmarc_result=dmarc,
        attachments=tuple(attachments),
        skipped_attachments=skipped,
    )
