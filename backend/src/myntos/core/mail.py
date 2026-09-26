"""Sending mail, and the decision not to.

Myntos sends one kind of message today: an invitation to join an organization.
That is enough to want a real transport and not enough to want a dependency, so
sending sits behind a small protocol with two implementations: the standard
library's ``smtplib``, run off the event loop in a worker thread, and Cloudflare
Email Sending over HTTPS for a deployment whose domain is on Cloudflare anyway.

**Both send the same bytes.** :func:`build_message` composes the MIME message
once, and Cloudflare's ``send_raw`` endpoint takes that message rather than a
set of JSON fields it would re-compose its own way. So the transport is a choice
about how a message leaves the building and never about what it says: a
rendering bug reproduces identically on either, and there is one place to fix
it.

Four things are deliberate.

**A deployment without SMTP settings is a supported deployment.** The invitation
flow worked before there was any mail at all, by handing the inviter a link to
pass on themselves, and it still does. Nothing here raises when the settings are
absent; :class:`NullMailer` reports "not sent" and the caller says so in the
interface rather than pretending.

**A failure to send is not a failure to invite.** The invitation exists in the
database the moment it is created, and the mail is a courtesy on top of it. A
refused connection, a greylisting relay or a timeout must not roll back a
membership decision an administrator already made, so :meth:`Mailer.send`
answers with a result rather than an exception, and the API reports whether the
message went out.

**The address is never trusted into a header.** Everything that reaches a header
goes through :func:`_header_safe`, because a newline in a display name is a
header injection: an invitee who registered as ``Kari\\nBcc: everyone@`` would
otherwise write their own recipients.

**A bounce is not a send.** Cloudflare answers a rejected recipient with a
``200`` and the address under ``permanent_bounces``, which is a successful
request about a message that will never arrive. Reported as sent, a typo in an
invited address would take the copy-this-link fallback away from the one inviter
who needs it, so a bounce is reported as a failure to send.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from myntos.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InlineImage:
    """A picture that travels inside the message, referred to as ``cid:``.

    Related to the HTML part rather than attached to the message, which is the
    difference between an image the client draws in place and one it lists at
    the bottom as a file. Never a remote URL: see :mod:`myntos.core.brand` for
    why a logo fetched over HTTP is both a broken image and a read receipt.
    """

    # Matches the ``cid:`` reference in the HTML, without the angle brackets the
    # header needs. Those are added where the part is built.
    cid: str
    data: bytes
    # The image subtype, as in ``image/png``.
    subtype: str = "png"


@dataclass(frozen=True, slots=True)
class Mail:
    """One message, composed but not yet addressed to a transport."""

    to: str
    subject: str
    text: str
    # Optional on purpose: a message must be readable as text, and the HTML part
    # is an alternative to it rather than the message itself.
    html: str | None = None
    # Set when a reply should reach a person rather than the sending mailbox.
    # The inviter's own address, for an invitation: the recipient's first
    # question is usually for them.
    reply_to: str | None = None
    # Pictures the HTML part refers to by ``cid:``. Ignored without an HTML
    # part, because there would be nothing to relate them to; a text-only
    # message is still a complete message.
    images: tuple[InlineImage, ...] = ()


@dataclass(frozen=True, slots=True)
class Delivery:
    """What happened to a message. Never raised, always returned."""

    sent: bool
    # Why not, in prose meant for a log rather than for a recipient. None when
    # it was sent, and when it was not because nothing is configured, which is a
    # deployment's choice and not an incident.
    error: str | None = None


class Mailer(Protocol):
    """Somewhere to hand a composed message."""

    @property
    def configured(self) -> bool:
        """Whether this mailer can send anything at all."""

    async def send(self, mail: Mail) -> Delivery: ...


def _header_safe(value: str) -> str:
    """Strip anything that could start a new header line.

    Names arrive from user input (an organization's name, a person's full name)
    and end up in Subject and From. A CR or LF in either is how a message grows
    recipients nobody chose.
    """
    return value.replace("\r", " ").replace("\n", " ").strip()


def build_message(mail: Mail, settings: Settings) -> EmailMessage:
    """Turn a :class:`Mail` into the message that goes on the wire.

    Separate from sending so the composition can be asserted in tests without a
    server, and so a deployment with no SMTP host can still be checked for what
    it *would* have said.
    """
    sender = settings.mail_from
    if sender is None:  # pragma: no cover - guarded by Settings.mail_configured
        raise ValueError("mail_from is not configured")

    message = EmailMessage()
    local, _, domain = sender.partition("@")
    message["From"] = Address(_header_safe(settings.mail_from_name), local, domain)
    message["To"] = mail.to
    message["Subject"] = _header_safe(mail.subject)
    reply_to = mail.reply_to or settings.mail_reply_to
    if reply_to is not None:
        message["Reply-To"] = _header_safe(reply_to)
    # Text first, then HTML as the alternative: a client that renders neither
    # still shows something a person can read and act on.
    message.set_content(mail.text)
    if mail.html is not None:
        message.add_alternative(mail.html, subtype="html")
        _relate_images(message, mail.images)
    return message


def _relate_images(message: EmailMessage, images: Sequence[InlineImage]) -> None:
    """Hang ``images`` off the HTML part as ``multipart/related``.

    They have to be related to the HTML alternative, not to the message: a
    ``cid:`` reference resolves within the part that carries it, and an image
    added at the top level is an attachment the reader sees as a file, listed
    at the bottom rather than drawn in place.

    ``get_body`` is how the part is found rather than indexing the payload,
    because it asks the question by name (give me the HTML body) instead of
    depending on ``add_alternative`` having appended it last.
    """
    if not images:
        return
    html_part = message.get_body(preferencelist=("html",))
    if html_part is None:  # pragma: no cover - build_message just added one
        return
    for image in images:
        html_part.add_related(
            image.data,
            maintype="image",
            subtype=image.subtype,
            # Angle brackets: RFC 2392 addresses ``cid:x`` to a part whose
            # Content-ID header is ``<x>``. add_related adds them if they are
            # missing, but writing them is what makes the pairing readable.
            cid=f"<{image.cid}>",
            # Named, because a client that lists parts anyway should list
            # something a person recognises rather than a bare id.
            filename=f"{image.cid}.{image.subtype}",
        )


class NullMailer:
    """The mailer for a deployment that has configured no transport at all.

    It reports what it would have sent, at debug level, and answers "not sent".
    Silence would be worse: the interface tells the user their colleague was
    *not* emailed, and a developer reading the log should be able to see the
    message that did not go.
    """

    configured = False

    async def send(self, mail: Mail) -> Delivery:
        logger.debug(
            "mail not sent (no transport configured): to=%s subject=%s", mail.to, mail.subject
        )
        return Delivery(sent=False)


class SmtpMailer:
    """SMTP, in a worker thread.

    ``smtplib`` is blocking, and a slow relay would otherwise hold the event
    loop for as long as it takes to answer. One connection per message: Myntos
    sends a handful of invitations a day, and a pooled connection would be a
    cache to invalidate for no gain.
    """

    configured = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, mail: Mail) -> Delivery:
        message = build_message(mail, self._settings)
        try:
            await asyncio.to_thread(self._deliver, message)
        except (OSError, smtplib.SMTPException) as error:
            # Logged here and reported to the caller, never raised: the
            # invitation this message is about has already been made.
            logger.warning("could not send mail to %s: %s", mail.to, error)
            return Delivery(sent=False, error=str(error))
        logger.info("sent mail to %s: %s", mail.to, message["Subject"])
        return Delivery(sent=True)

    def _deliver(self, message: EmailMessage) -> None:
        settings = self._settings
        host = settings.smtp_host
        assert host is not None  # noqa: S101 - guarded by Settings.mail_configured
        timeout = settings.smtp_timeout_seconds

        client: smtplib.SMTP | smtplib.SMTP_SSL
        if settings.smtp_tls_mode == "ssl":
            client = smtplib.SMTP_SSL(
                host, settings.smtp_port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(host, settings.smtp_port, timeout=timeout)
        with client:
            if settings.smtp_tls_mode == "starttls":
                # Verified against the system trust store, which is what
                # create_default_context does. A relay whose certificate does
                # not check out is a relay reading the mail.
                client.starttls(context=ssl.create_default_context())
            if settings.smtp_username is not None and settings.smtp_password is not None:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            client.send_message(message)


# Longer than a page load and shorter than the inviter's patience. The send is
# inline in the invitation request (see the endpoint that creates it), so this is
# time somebody spends watching a button spin; a relay slower than this has
# already failed as far as the interface is concerned, and the invitation itself
# is saved either way.
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _client() -> httpx.AsyncClient:
    """The HTTP client one message goes out on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class CloudflareMailer:
    """Cloudflare Email Sending, over HTTPS.

    The ``send_raw`` endpoint rather than the field-by-field one: it takes the
    MIME message :func:`build_message` already produces, so this transport
    composes nothing of its own and cannot drift from SMTP's rendering. The
    ``from`` field alongside it is the SMTP envelope sender, which is where a
    bounce goes; the header the recipient sees is inside the message.

    No client is kept between messages, for the reason :class:`SmtpMailer` keeps
    no connection: a handful of invitations a day does not earn a pool.
    """

    configured = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, mail: Mail) -> Delivery:
        settings = self._settings
        message = build_message(mail, settings)
        assert settings.cloudflare_account_id is not None  # noqa: S101 - see mail_configured
        assert settings.cloudflare_email_token is not None  # noqa: S101 - see mail_configured
        assert settings.mail_from is not None  # noqa: S101 - see mail_configured
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{settings.cloudflare_account_id}/email/sending/send_raw"
        )
        try:
            async with _client() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": (
                            f"Bearer {settings.cloudflare_email_token.get_secret_value()}"
                        )
                    },
                    json={
                        "from": settings.mail_from,
                        "recipients": [mail.to],
                        "mime_message": message.as_string(),
                    },
                )
        except httpx.HTTPError as error:
            # Same contract as SmtpMailer: logged, returned, never raised. The
            # invitation this message is about has already been made.
            logger.warning("could not send mail to %s: %s", mail.to, error)
            return Delivery(sent=False, error=str(error))

        problem = _cloudflare_problem(response, recipient=mail.to)
        if problem is not None:
            logger.warning("could not send mail to %s: %s", mail.to, problem)
            return Delivery(sent=False, error=problem)
        logger.info("sent mail to %s: %s", mail.to, message["Subject"])
        return Delivery(sent=True)


def _cloudflare_problem(response: httpx.Response, *, recipient: str) -> str | None:
    """Why that message did not go, or ``None`` if it did.

    Three ways to fail, and only the first looks like one. An HTTP error is
    reported by status; a refusal is reported as ``200`` with ``success: false``
    and a list of errors; and a rejected recipient is reported as an outright
    success carrying the address under ``permanent_bounces``.

    A body that does not parse is treated as a failure rather than as a send.
    Guessing "probably fine" here would tell an inviter their colleague was
    emailed on the strength of a response nobody could read.
    """
    if response.status_code >= 400:
        # Cloudflare's error bodies are small and say which field is wrong;
        # truncated because this reaches a log line, not a screen.
        return f"HTTP {response.status_code}: {response.text[:500]}"
    try:
        body: Any = response.json()
    except ValueError:
        return f"unreadable response: {response.text[:200]!r}"
    if not isinstance(body, dict):
        return f"unexpected response: {body!r:.200}"
    if not body.get("success", False):
        errors = body.get("errors") or []
        detail = "; ".join(
            str(item.get("message", item)) if isinstance(item, dict) else str(item)
            for item in errors
        )
        return detail or "refused without saying why"
    result = body.get("result")
    bounced = result.get("permanent_bounces") or [] if isinstance(result, dict) else []
    if recipient in bounced:
        # The address itself is wrong, and no retry changes that.
        return "rejected by the receiving server (permanent bounce)"
    return None


def build_mailer(settings: Settings | None = None) -> Mailer:
    """The mailer this deployment's configuration asks for.

    SMTP wins when both are configured, because naming a relay is the more
    specific act: Cloudflare's credentials may be present for storage-shaped
    reasons, while an ``SMTP_HOST`` was typed to send mail through.
    """
    resolved = settings if settings is not None else get_settings()
    if resolved.smtp_configured:
        return SmtpMailer(resolved)
    if resolved.cloudflare_mail_configured:
        return CloudflareMailer(resolved)
    return NullMailer()
