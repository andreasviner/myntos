"""The invitation email: that it is sent, what it says, and when it is not.

Two halves. The composition is pure and tested directly, because the wording is
the product here: a message that reaches an inbox and does not say who is asking
has failed even though it was delivered. The endpoint half is about the promise
the interface makes — an invitation is created whether or not the mail goes out,
and the response says honestly which happened.
"""

import email
import email.policy
import email.utils
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.core import mail as mail_module
from myntos.core.brand import LOGO_CID, logo_png
from myntos.core.config import Settings
from myntos.core.mail import (
    CloudflareMailer,
    Mail,
    SmtpMailer,
    build_mailer,
    build_message,
)
from myntos.domain.identity.access import Permission, permissions_for
from myntos.domain.identity.invitation_mail import ROLE_WORDS, invitation_mail
from myntos.domain.identity.models import OrganizationRole
from myntos.main import create_app
from tests.conftest import TEST_ORIGIN, RecordingMailer, register, write


def _books(client: TestClient, name: str = "Fjord Regnskap AS") -> int:
    """Register an owner and set up one company. Returns the company id.

    An invitation is to a set of books, so every test here needs one to invite
    somebody to.
    """
    owner = register(client, "kari@example.no")
    created = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": owner["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": name,
        },
    )
    assert created.status_code == 201, created.text
    company_id: int = created.json()["id"]
    return company_id


def _invite(
    client: TestClient, company_id: int, email: str, role: str = "member"
) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- What the message says -------------------------------------------------------


def _sample(**overrides: Any) -> Mail:
    fields: dict[str, Any] = {
        "to": "ola@example.no",
        "company": "Fjord Regnskap AS",
        "role": OrganizationRole.MEMBER,
        "invite_url": "https://app.example/invitasjon/?token=abc123",
        "expires_at": datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        "inviter_name": "Kari Nordmann",
        "inviter_email": "kari@example.no",
    }
    fields.update(overrides)
    return invitation_mail(**fields)


def test_the_message_answers_who_what_and_by_when() -> None:
    """The three questions a stranger opening this actually has."""
    mail = _sample()

    assert "Kari Nordmann" in mail.text
    assert "Fjord Regnskap AS" in mail.text
    assert "https://app.example/invitasjon/?token=abc123" in mail.text
    # A date, not "utløper om 7 dager": the reader decides when to act, and a
    # duration counted from a moment they cannot see is not a deadline.
    assert "9. september 2026" in mail.text
    assert "Fjord Regnskap AS" in mail.subject


def test_the_link_survives_a_client_that_strips_the_button() -> None:
    """The HTML part writes the URL out as text as well as wrapping it."""
    mail = _sample()
    assert mail.html is not None
    # Twice: once as the button's href, once written out underneath.
    assert mail.html.count("https://app.example/invitasjon/?token=abc123") >= 2


def test_an_unknown_inviter_does_not_produce_a_blank() -> None:
    """The account that sent it can be gone; the invitation is still valid."""
    mail = _sample(inviter_name=None)
    assert "Du er invitert til å føre regnskapet for Fjord Regnskap AS" in mail.text
    assert "None" not in mail.text


def test_a_name_with_markup_in_it_is_escaped() -> None:
    """A company is named by whoever set its books up."""
    mail = _sample(company="<script>alert(1)</script> AS")
    assert mail.html is not None
    assert "<script>" not in mail.html
    assert "&lt;script&gt;" in mail.html


def test_the_message_names_the_colleague_to_ask_but_not_as_reply_to() -> None:
    """Somebody to ask whether it is genuine, in the text where a filter does not score it.

    It was the Reply-To until every invitation carrying one was refused as spam
    in October 2026: a Reply-To on another domain than From is the shape of a
    phishing message (invitation_mail's module docstring).
    """
    mail = _sample()
    assert mail.reply_to is None
    assert "spørre Kari Nordmann på kari@example.no" in mail.text
    assert mail.html is not None
    assert "spørre Kari Nordmann på kari@example.no" in mail.html


def test_an_unnamed_inviter_is_still_somebody_to_ask() -> None:
    mail = _sample(inviter_name=None)
    assert "spørre avsenderen på kari@example.no" in mail.text
    assert "None" not in mail.text


def test_without_a_confirmed_address_there_is_nobody_to_ask() -> None:
    """The endpoint passes no address for an unconfirmed sender; the line goes with it."""
    mail = _sample(inviter_email=None)
    assert "usikker på om invitasjonen er ekte" not in mail.text
    assert mail.html is not None
    assert "usikker på om invitasjonen er ekte" not in mail.html


def test_every_role_has_words() -> None:
    """A role added to the enum without wording would raise on send."""
    assert set(ROLE_WORDS) == set(OrganizationRole)


def test_the_words_do_not_promise_more_than_the_role_carries() -> None:
    """An invitation that overstates the role is worse than one that says nothing.

    Three claims the wording makes, each checked against the permission that
    decides it, so a change to the role table cannot leave the email describing
    the old one.
    """
    for role, (_word, blurb) in ROLE_WORDS.items():
        held = permissions_for(role)
        assert ("ikke endre" in blurb) == (Permission.LEDGER_WRITE not in held), role
        assert ("medlemmer" in blurb) == (Permission.MEMBER_MANAGE in held), role
        assert ("abonnement" in blurb) == (Permission.BILLING_MANAGE in held), role


# --- The envelope ----------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "mail_from": "ingen-svar@myntos.no",
        "mail_from_name": "Myntos",
        "smtp_host": "smtp.example",
    }
    fields.update(overrides)
    return Settings(**fields)


def test_the_message_is_addressed_from_the_configured_sender() -> None:
    message = build_message(_sample(), _settings())
    assert message["From"] == "Myntos <ingen-svar@myntos.no>"
    assert message["To"] == "ola@example.no"
    assert message["Reply-To"] is None


def test_the_message_carries_a_date_and_a_message_id() -> None:
    """RFC 5322 requires both, and a spam filter scores their absence.

    Neither was set until 2026-10-23, when Cloudflare's filter was refusing the
    invitations at its threshold and passing the account mails one point under
    it. The Message-ID is at the sender's own domain: a host name would put
    this machine's in every message.
    """
    message = build_message(_sample(), _settings())
    assert message["Date"] is not None
    assert email.utils.parsedate_to_datetime(str(message["Date"])).tzinfo is not None
    assert str(message["Message-ID"]).endswith("@myntos.no>")
    assert build_message(_sample(), _settings())["Message-ID"] != message["Message-ID"]


def test_the_message_carries_both_a_text_and_an_html_part() -> None:
    """Text first, then the HTML alternative, which now carries the mark with it.

    The HTML part is wrapped in a ``multipart/related`` rather than sitting
    beside the text directly, because that is where a ``cid:`` reference
    resolves. A client that renders neither still finds the text/plain in the
    same place it always was.
    """
    message = build_message(_sample(), _settings())
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert parts == ["text/plain", "multipart/related"]

    related = list(message.iter_parts())[1]
    assert [part.get_content_type() for part in related.iter_parts()] == [
        "text/html",
        "image/png",
    ]


def test_the_logo_travels_inside_the_message() -> None:
    """Never a URL: a remote logo is a blocked image and a read receipt.

    The Content-ID on the part has to be the one the markup asks for, angle
    brackets and all, or the client shows a broken image next to a stranger's
    link, which is the one thing this message cannot afford to look like.
    """
    mail = _sample()
    assert mail.images != ()
    assert mail.html is not None
    assert f"cid:{LOGO_CID}" in mail.html
    assert "http://" not in mail.html
    assert "<img" in mail.html and 'src="https://' not in mail.html

    message = build_message(mail, _settings())
    logo = next(part for part in message.walk() if part.get_content_type() == "image/png")
    assert logo["Content-ID"] == f"<{LOGO_CID}>"
    assert logo.get_payload(decode=True) == logo_png()


def test_a_blocked_image_still_leaves_the_sender_named() -> None:
    """Every client blocks something. The word has to survive the picture."""
    mail = _sample()
    assert mail.html is not None
    assert 'alt="Myntos"' in mail.html
    # And beside the mark as text, not only in the alt attribute.
    assert mail.html.count("Myntos") >= 2


def test_the_message_greets_the_person_it_was_addressed_to() -> None:
    """First name only, and only when the inviter named somebody."""
    named = _sample(recipient_name="Andreas Edvardsen Lindeman")
    assert named.text.startswith("Hei Andreas,")
    assert named.html is not None
    assert "Hei Andreas," in named.html
    # Not the whole register spelling, which is how a form letter reads.
    assert "Hei Andreas Edvardsen" not in named.text

    anonymous = _sample()
    assert "Hei" not in anonymous.text.split("\n")[0]


def test_a_resend_says_that_the_old_link_is_dead() -> None:
    """Two identical emails are a bug report. One that explains itself is not."""
    again = _sample(resent=True)
    assert "på nytt" in again.subject or "Påminnelse" in again.subject
    assert "virker ikke lenger" in again.text
    assert again.html is not None
    assert "virker ikke lenger" in again.html


def test_a_newline_in_a_name_cannot_add_a_header() -> None:
    """Header injection: the display name is configuration, but so was every
    other value somebody once trusted."""
    message = build_message(
        _sample(company="Fjord AS"), _settings(mail_from_name="Myntos\nBcc: alle@example.no")
    )
    assert message["Bcc"] is None
    assert "\n" not in str(message["From"])


def test_a_deployment_with_no_transport_gets_the_mailer_that_sends_nothing() -> None:
    assert build_mailer(_settings(smtp_host=None)).configured is False
    assert build_mailer(_settings(mail_from=None)).configured is False
    assert build_mailer(_settings()).configured is True


# --- Cloudflare Email Sending ----------------------------------------------------
#
# The transport for the deployment whose domain is on Cloudflare already. The
# tests below are about the request that leaves and the answer that comes back,
# because that is all this transport is: the message itself is composed by
# build_message and asserted above, once, for both transports.


def _cloudflare_settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "mail_from": "no-reply@myntos.no",
        "mail_from_name": "Myntos",
        "smtp_host": None,
        "cloudflare_account_id": "acc123",
        "cloudflare_email_token": "tok-secret",
    }
    fields.update(overrides)
    return Settings(**fields)


def _cloudflare(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    **overrides: Any,
) -> CloudflareMailer:
    """A mailer whose requests go to `handler` instead of to Cloudflare."""
    monkeypatch.setattr(
        mail_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return CloudflareMailer(_cloudflare_settings(**overrides))


def _readable(mime: str) -> tuple[dict[str, str], str, str]:
    """A MIME message reduced to what two of them can be compared on.

    Not the bytes: :mod:`email` picks a random multipart boundary per message,
    so two compositions of the same mail never match as strings. The headers and
    both rendered parts do, and they are what "the same message" means here.
    """
    parsed = email.message_from_string(mime, policy=email.policy.default)
    # Nor the Date and Message-ID, which differ between two compositions by
    # design: a Message-ID that repeated would not identify anything.
    headers = {
        name: str(value)
        for name, value in parsed.items()
        if name not in ("Content-Type", "Date", "Message-ID")
    }
    plain = parsed.get_body(("plain",))
    html = parsed.get_body(("html",))
    assert plain is not None and html is not None
    return headers, plain.get_content(), html.get_content()


def _accepted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "result": {"delivered": [], "permanent_bounces": [], "queued": ["ola@example.no"]},
        },
    )


async def test_the_request_carries_the_message_smtp_would_have_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason for using send_raw rather than the field-by-field endpoint.

    What goes on the wire is the MIME message build_message produced, so the
    Subject, the Message-ID and both body parts are the ones asserted in the
    section above rather than Cloudflare's rendering of a JSON object.
    """
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _accepted(request)

    delivery = await _cloudflare(monkeypatch, record).send(_sample())

    assert delivery.sent is True
    (request,) = seen
    assert request.url.path == "/client/v4/accounts/acc123/email/sending/send_raw"
    body = json.loads(request.content)
    # The envelope sender, which is where a bounce goes. The address the
    # recipient sees is a header inside the message.
    assert body["from"] == "no-reply@myntos.no"
    assert body["recipients"] == ["ola@example.no"]
    mime = body["mime_message"]
    expected = build_message(_sample(), _cloudflare_settings()).as_string()
    assert _readable(mime) == _readable(expected)
    headers, plain, _html = _readable(mime)
    assert "Reply-To" not in headers
    assert email.message_from_string(mime)["Message-ID"] is not None
    assert headers["From"] == "Myntos <no-reply@myntos.no>"
    assert "Fjord Regnskap AS" in plain


async def test_a_send_is_logged_by_cloudflares_id_and_not_by_address(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The id is what Cloudflare's analytics are searched by when nothing arrived.

    And "queued", not "sent": Cloudflare's spam filter runs after this answer,
    which is how three invitations reported as sent were refused in October
    2026. No address, because the journal is kept longer than an account is.
    """

    def queued(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "errors": [],
                "result": {
                    "message_id": "<abc123@myntos.no>",
                    "delivered": [],
                    "permanent_bounces": [],
                    "queued": ["ola@example.no"],
                },
            },
        )

    with caplog.at_level(logging.INFO, logger="myntos.core.mail"):
        delivery = await _cloudflare(monkeypatch, queued).send(_sample())

    assert delivery.sent is True
    assert "mail queued by Cloudflare: <abc123@myntos.no>" in caplog.text
    assert "ola@example.no" not in caplog.text


def test_the_apps_own_log_lines_reach_stderr_once() -> None:
    """uvicorn configures its loggers and nobody else's, so ours went nowhere below WARNING."""
    create_app()
    create_app()
    package = logging.getLogger("myntos")
    ours = [handler for handler in package.handlers if getattr(handler, "_myntos_own", False)]
    assert len(ours) == 1
    assert package.isEnabledFor(logging.INFO)


async def test_the_token_is_sent_as_a_bearer_and_not_in_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _accepted(request)

    await _cloudflare(monkeypatch, record).send(_sample())

    (request,) = seen
    assert request.headers["Authorization"] == "Bearer tok-secret"
    assert "tok-secret" not in request.content.decode()


async def test_a_rejected_address_is_reported_as_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanent bounce arrives as a 200, and it is not a send.

    This is the mistyped invitee, which is the common case: the request
    succeeded, the message will never arrive, and the inviter needs the
    copy-this-link fallback that only appears when we admit it did not go.
    """
    bounced = httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "result": {
                "delivered": [],
                "permanent_bounces": ["ola@example.no"],
                "queued": [],
            },
        },
    )

    delivery = await _cloudflare(monkeypatch, lambda _request: bounced).send(_sample())

    assert delivery.sent is False
    assert delivery.error is not None
    assert "bounce" in delivery.error


async def test_a_refusal_says_which_one_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """`success: false` under a 200, which is how Cloudflare reports a bad request.

    The message is kept because it names the cause — an unverified sender domain
    reads very differently from a malformed address, and the log line is the
    only place anybody will see it.
    """
    refused = httpx.Response(
        200,
        json={
            "success": False,
            "errors": [{"code": 1000, "message": "Sender domain not verified"}],
            "result": None,
        },
    )

    delivery = await _cloudflare(monkeypatch, lambda _request: refused).send(_sample())

    assert delivery.sent is False
    assert delivery.error == "Sender domain not verified"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"success": False, "errors": [{"message": "Invalid token"}]}),
        httpx.Response(500, text="upstream trouble"),
        # A 200 that is not JSON at all: a proxy's error page, say. Treated as a
        # failure rather than guessed to be fine, because telling an inviter
        # their colleague was emailed on the strength of an unreadable answer is
        # the one outcome this module refuses.
        httpx.Response(200, text="<html>nope</html>"),
    ],
    ids=["unauthorized", "server error", "unreadable"],
)
async def test_nothing_that_is_not_an_accepted_message_counts_as_sent(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    delivery = await _cloudflare(monkeypatch, lambda _request: response).send(_sample())

    assert delivery.sent is False
    assert delivery.error


async def test_an_unreachable_api_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same contract as a refused SMTP connection: the invitation survives it."""

    def unreachable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    delivery = await _cloudflare(monkeypatch, unreachable).send(_sample())

    assert delivery.sent is False
    assert delivery.error is not None
    assert "connection refused" in delivery.error


def test_cloudflare_alone_is_a_configured_deployment() -> None:
    """No SMTP host anywhere, and mail still goes out."""
    assert _cloudflare_settings().mail_configured is True
    assert isinstance(build_mailer(_cloudflare_settings()), CloudflareMailer)


def test_half_a_cloudflare_configuration_sends_nothing() -> None:
    """Treated as unconfigured rather than as failing at the first invitation."""
    assert build_mailer(_cloudflare_settings(cloudflare_email_token=None)).configured is False
    assert build_mailer(_cloudflare_settings(cloudflare_account_id=None)).configured is False
    assert build_mailer(_cloudflare_settings(mail_from=None)).configured is False


def test_a_named_smtp_host_wins_over_cloudflare() -> None:
    """Both configured is a deployment that went out of its way to name a relay."""
    both = _cloudflare_settings(smtp_host="smtp.example")
    assert isinstance(build_mailer(both), SmtpMailer)


# --- Sending it, from the endpoint that creates the invitation --------------------


def test_inviting_a_colleague_emails_them(client: TestClient, outbox: RecordingMailer) -> None:
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up

    invitation = _invite(client, company_id, "ola@example.no", "admin")

    assert invitation["email_sent"] is True
    message = outbox.only
    assert message.to == "ola@example.no"
    # The link in the message is the link the response returned, or the two
    # halves of this feature are describing different invitations.
    assert invitation["invite_url"] in message.text
    assert "Kari Nordmann" in message.text
    assert "administrator" in message.text


def test_the_invitation_still_works_when_the_relay_refuses(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """A mail server having a bad afternoon is not a reason to lose the decision.

    The invitation is created, the response says the message did not go, and the
    link is there for the inviter to pass on themselves.
    """
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up
    outbox.fails_with = "451 greylisted, try again later"

    invitation = _invite(client, company_id, "ola@example.no")

    assert invitation["email_sent"] is False
    assert invitation["invite_url"].startswith("https://app.example/invitasjon/?token=")
    # And it is a real invitation: the colleague can still use the link.
    colleague = TestClient(client.app, base_url=TEST_ORIGIN)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]
    accepted = write(colleague, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text


def test_a_deployment_that_sends_no_mail_says_so(
    client: TestClient, outbox: RecordingMailer
) -> None:
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up
    outbox.configured = False

    invitation = _invite(client, company_id, "ola@example.no")

    assert invitation["email_sent"] is False
    assert invitation["invite_url"] is not None
    assert outbox.sent == []


def test_listing_invitations_neither_resends_nor_claims_to_have(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """The link and the send are for the moment of invitation, and only then."""
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up
    _invite(client, company_id, "ola@example.no")
    outbox.sent.clear()

    listed = client.get(f"/api/v1/companies/{company_id}/invitations").json()

    assert [item["invite_url"] for item in listed] == [None]
    assert [item["email_sent"] for item in listed] == [None]
    assert outbox.sent == []


def test_reinviting_sends_a_second_message_with_the_new_link(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """Re-inviting is how an invitation is resent, and the old link stops working."""
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up

    first = _invite(client, company_id, "ola@example.no", "viewer")
    second = _invite(client, company_id, "ola@example.no", "member")

    assert len(outbox.sent) == 2
    assert first["invite_url"] != second["invite_url"]
    assert second["invite_url"] in outbox.sent[1].text
    assert "medlem" in outbox.sent[1].text


@pytest.mark.parametrize("role", ["viewer", "member", "admin", "owner"])
def test_the_message_names_the_role_the_invitation_grants(
    client: TestClient, outbox: RecordingMailer, role: str
) -> None:
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up

    _invite(client, company_id, "ola@example.no", role)

    word, _blurb = ROLE_WORDS[OrganizationRole(role)]
    assert word in outbox.only.text


def test_a_member_who_may_not_invite_sends_no_mail(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """The permission check comes first: a refusal must not still email somebody."""
    company_id = _books(client)
    outbox.sent.clear()  # the owner's own confirmation mail from signing up
    invitation = _invite(client, company_id, "ola@example.no", "viewer")
    colleague = TestClient(client.app, base_url=TEST_ORIGIN)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]
    write(colleague, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    outbox.sent.clear()

    refused = write(
        colleague,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": "tredje@example.no", "role": "member"},
    )

    assert refused.status_code == 403, refused.text
    assert outbox.sent == []


# --- The welcome page's lookup ---------------------------------------------------


def _lookup(client: TestClient, token: str) -> Any:
    return client.post("/api/v1/auth/invitations/lookup", json={"token": token})


def test_the_link_says_who_invited_you_before_you_sign_in(client: TestClient) -> None:
    """The welcome page's whole reason for existing.

    Answered without a session: the recipient has no account yet, and being met
    by a login form that will not say what it is for is how an invitation gets
    read as phishing.
    """
    company_id = _books(client)
    invitation = _invite(client, company_id, "ola@example.no", "admin")
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]

    anonymous = TestClient(client.app, base_url=TEST_ORIGIN)
    described = _lookup(anonymous, token)

    assert described.status_code == 200, described.text
    body = described.json()
    # The books being shared, which is what the invitation is for. Never the
    # account that sent it: the invitee gets this company and nothing else.
    assert body["company_name"] == "Fjord Regnskap AS"
    assert body["inviter_name"] == "Kari Nordmann"
    assert body["email"] == "ola@example.no"
    assert body["role"] == "admin"


def test_a_spent_invitation_is_refused_like_an_unknown_one(client: TestClient) -> None:
    """One answer for every unusable token, so a guess cannot be confirmed."""
    company_id = _books(client)
    invitation = _invite(client, company_id, "ola@example.no")
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]
    colleague = TestClient(client.app, base_url=TEST_ORIGIN)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    write(colleague, "POST", "/api/v1/auth/invitations/accept", json={"token": token})

    spent = _lookup(client, token)
    never_existed = _lookup(client, "ikke-en-token")

    assert spent.status_code == never_existed.status_code == 400
    assert spent.json()["code"] == never_existed.json()["code"] == "invalid_invitation"


def test_an_expired_invitation_is_refused(client: TestClient, database_url: str) -> None:
    """The deadline the email states is the deadline the link honours."""
    company_id = _books(client)
    invitation = _invite(client, company_id, "ola@example.no")
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]

    # The expiry is moved into the past rather than the clock into the future:
    # the invitation row is the thing under test, and a suite that travels in
    # time breaks every session in the same request. Both timestamps move,
    # because the table checks that an invitation expires after it was made.
    engine = sa.create_engine(database_url.replace("sqlite+aiosqlite", "sqlite"))
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE organization_invitation SET created_at = :made, expires_at = :expired"),
            {"made": now - timedelta(days=8), "expired": now - timedelta(days=1)},
        )
    engine.dispose()

    refused = _lookup(client, token)
    assert refused.status_code == 400
    assert refused.json()["code"] == "invalid_invitation"
