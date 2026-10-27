"""Address confirmation and password reset: the links Myntos mails a person about their own account.

The BankID side of these (a reset request for a BankID account, the switch to
e-mail and back) runs end to end against a stub broker in test_auth_bankid.py;
what the messages themselves say is checked at the bottom of this file.

Three promises are tested harder than the happy path, because each is the kind
that holds in a demo and breaks in production:

- a link works once, for a limited time, for one purpose, and the newest one
  is the only live one;
- the reset request answers an address with an account exactly as it answers
  one without, in status, body and in whether it waited on a mail relay;
- a deployment that sends no mail behaves exactly as it did before any of this
  existed, and an account that never confirmed is locked out of nothing.
"""

from datetime import timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.core import mail as mail_module
from myntos.core.clock import utc_now
from myntos.core.config import Settings
from myntos.core.mail import CloudflareMailer
from myntos.domain.identity import email_tokens
from myntos.domain.identity.account_mail import (
    bankid_sign_in_mail,
    password_reset_mail,
    switch_to_email_mail,
    switched_to_bankid_mail,
    verification_mail,
)
from tests.conftest import PASSWORD, TEST_ORIGIN, RecordingMailer, register, write

NEW_PASSWORD = "et-helt-annet-langt-passord"


def _token_in(text: str, page: str) -> str:
    """The token from the link to ``page`` in a message body."""
    marker = f"https://app.example{page}#token="
    assert marker in text, text
    return text.split(marker, maxsplit=1)[1].split()[0]


def _sync_engine(database_url: str) -> sa.Engine:
    return sa.create_engine(database_url.replace("sqlite+aiosqlite", "sqlite"))


def _later(monkeypatch: pytest.MonkeyPatch, delta: timedelta) -> None:
    """Move the token module's clock forward, as if the link sat in an inbox."""
    moment = utc_now() + delta
    monkeypatch.setattr(email_tokens, "utc_now", lambda: moment)


def _forgot(client: TestClient, email: str) -> Any:
    return write(client, "POST", "/api/v1/auth/password/forgot", json={"email": email})


def _reset(client: TestClient, token: str, password: str = NEW_PASSWORD) -> Any:
    return write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": password},
    )


def _login(client: TestClient, password: str, email: str = "kari@example.no") -> Any:
    return write(client, "POST", "/api/v1/auth/login", json={"email": email, "password": password})


# --- Confirming the address -------------------------------------------------------


def test_signing_up_mails_a_confirmation_link(client: TestClient, outbox: RecordingMailer) -> None:
    body = register(client)

    assert body["user"]["email_verified"] is False
    message = outbox.only
    assert message.to == "kari@example.no"
    assert "Bekreft" in message.subject
    _token_in(message.text, "/bekreft-epost/")


def test_the_link_confirms_the_address(client: TestClient, outbox: RecordingMailer) -> None:
    register(client)
    token = _token_in(outbox.only.text, "/bekreft-epost/")

    # From a different browser with no session: the link is the whole proof.
    stranger = TestClient(client.app, base_url=TEST_ORIGIN)
    confirmed = write(stranger, "POST", "/api/v1/auth/email/verify", json={"token": token})

    assert confirmed.status_code == 204, confirmed.text
    # And it signed nobody in.
    assert stranger.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").json()["user"]["email_verified"] is True


def test_a_confirmation_link_works_once(client: TestClient, outbox: RecordingMailer) -> None:
    register(client)
    token = _token_in(outbox.only.text, "/bekreft-epost/")

    first = write(client, "POST", "/api/v1/auth/email/verify", json={"token": token})
    second = write(client, "POST", "/api/v1/auth/email/verify", json={"token": token})

    assert first.status_code == 204
    assert second.status_code == 400
    assert second.json()["code"] == "invalid_email_token"


def test_a_confirmation_link_expires(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(client)
    token = _token_in(outbox.only.text, "/bekreft-epost/")
    _later(monkeypatch, timedelta(hours=49))

    expired = write(client, "POST", "/api/v1/auth/email/verify", json={"token": token})

    assert expired.status_code == 400
    assert expired.json()["code"] == "invalid_email_token"


def test_asking_again_kills_the_previous_link(client: TestClient, outbox: RecordingMailer) -> None:
    register(client)
    old = _token_in(outbox.sent[0].text, "/bekreft-epost/")

    again = write(client, "POST", "/api/v1/auth/email/verification")
    assert again.status_code == 202, again.text
    assert again.json() == {"sent": True}
    new = _token_in(outbox.sent[1].text, "/bekreft-epost/")

    assert (
        write(client, "POST", "/api/v1/auth/email/verify", json={"token": old}).status_code == 400
    )
    assert (
        write(client, "POST", "/api/v1/auth/email/verify", json={"token": new}).status_code == 204
    )


def test_a_confirmed_address_is_not_confirmed_twice(
    client: TestClient, outbox: RecordingMailer
) -> None:
    register(client)
    token = _token_in(outbox.only.text, "/bekreft-epost/")
    write(client, "POST", "/api/v1/auth/email/verify", json={"token": token})

    again = write(client, "POST", "/api/v1/auth/email/verification")

    assert again.status_code == 409
    assert again.json()["code"] == "email_already_verified"


def test_asking_for_confirmation_mail_is_capped_per_hour(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """Three an hour, and the one sent at signup is the first of them."""
    register(client)
    for _ in range(2):
        assert write(client, "POST", "/api/v1/auth/email/verification").status_code == 202

    capped = write(client, "POST", "/api/v1/auth/email/verification")

    assert capped.status_code == 429
    assert capped.json()["code"] == "account_mail_rate_limited"
    assert len(outbox.sent) == 3


def test_asking_for_confirmation_needs_a_session(client: TestClient) -> None:
    assert write(client, "POST", "/api/v1/auth/email/verification").status_code == 401


def test_a_confirmation_link_cannot_reset_a_password(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """The purpose is part of the key, whatever else about the token is right."""
    register(client)
    token = _token_in(outbox.only.text, "/bekreft-epost/")

    refused = _reset(client, token)

    assert refused.status_code == 400
    assert _login(client, PASSWORD).status_code == 200


# --- A deployment with no mail ----------------------------------------------------


def test_without_mail_nothing_changes(client: TestClient, outbox: RecordingMailer) -> None:
    """Signup works, nothing is sent, and neither link can be asked for."""
    outbox.configured = False

    body = register(client)

    assert body["user"]["email_verified"] is False
    assert outbox.sent == []
    assert client.get("/api/v1/auth/providers").json()["email_delivery"] is False
    resend = write(client, "POST", "/api/v1/auth/email/verification")
    assert resend.status_code == 503
    forgot = _forgot(client, "kari@example.no")
    assert forgot.status_code == 503
    # The same answer for an address with no account: it is about the
    # deployment, not the address.
    assert _forgot(client, "ingen@example.no").status_code == 503


def test_an_unconfirmed_account_is_locked_out_of_nothing(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """Every password account made before this existed is unconfirmed."""
    register(client)
    write(client, "POST", "/api/v1/auth/logout")

    assert _login(client, PASSWORD).status_code == 200
    created = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": client.get("/api/v1/auth/me").json()["memberships"][0][
                "organization_id"
            ],
            "orgnr": "910000004",
            "name": "Fjord Regnskap AS",
        },
    )
    assert created.status_code == 201, created.text
    invited = write(
        client,
        "POST",
        f"/api/v1/companies/{created.json()['id']}/invitations",
        json={"email": "ola@example.no", "role": "member"},
    )
    assert invited.status_code == 201, invited.text


def test_an_invitation_from_an_unconfirmed_address_does_not_name_it(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """A question must not be sent to an address nobody has shown is theirs.

    And never as a Reply-To, confirmed or not: that is the header every refused
    invitation in October 2026 carried (invitation_mail's module docstring).
    """
    owner = register(client)
    confirmation = _token_in(outbox.only.text, "/bekreft-epost/")
    company = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": owner["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": "Fjord Regnskap AS",
        },
    ).json()
    outbox.sent.clear()

    url = f"/api/v1/companies/{company['id']}/invitations"
    write(client, "POST", url, json={"email": "ola@example.no", "role": "member"})
    assert outbox.only.reply_to is None
    assert "kari@example.no" not in outbox.only.text

    write(client, "POST", "/api/v1/auth/email/verify", json={"token": confirmation})
    outbox.sent.clear()
    write(client, "POST", url, json={"email": "per@example.no", "role": "member"})
    assert outbox.only.reply_to is None
    assert "på kari@example.no" in outbox.only.text


# --- Resetting a password ---------------------------------------------------------


def test_the_reset_request_does_not_say_whether_an_address_has_an_account(
    client: TestClient, outbox: RecordingMailer
) -> None:
    register(client)
    write(client, "POST", "/api/v1/auth/logout")
    outbox.sent.clear()

    known = _forgot(client, "kari@example.no")
    unknown = _forgot(client, "ingen@example.no")

    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content
    assert set(known.headers) == set(unknown.headers)
    # Only the real address got anything.
    assert [message.to for message in outbox.sent] == ["kari@example.no"]


def test_the_reset_mail_is_sent_after_the_response(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relay's latency must not be the tell the response body is careful not to be.

    Sending inline would make the known address the slow one. The send is a
    background task, which Starlette runs after the response has gone.
    """
    from fastapi import BackgroundTasks

    register(client)
    outbox.sent.clear()
    queued: list[Any] = []
    original = BackgroundTasks.add_task

    def spy(self: BackgroundTasks, func: Any, *args: Any, **kwargs: Any) -> None:
        queued.append(func)
        original(self, func, *args, **kwargs)

    monkeypatch.setattr(BackgroundTasks, "add_task", spy)

    _forgot(client, "kari@example.no")

    assert len(queued) == 1
    assert outbox.only.to == "kari@example.no"


def test_a_reset_sets_the_password_and_signs_everyone_out(
    client: TestClient, outbox: RecordingMailer
) -> None:
    register(client)
    elsewhere = TestClient(client.app, base_url=TEST_ORIGIN)
    assert _login(elsewhere, PASSWORD).status_code == 200
    outbox.sent.clear()

    _forgot(client, "kari@example.no")
    token = _token_in(outbox.only.text, "/nytt-passord/")
    done = _reset(TestClient(client.app, base_url=TEST_ORIGIN), token)

    assert done.status_code == 204, done.text
    # Every session is gone, the one that asked included.
    assert client.get("/api/v1/auth/me").status_code == 401
    assert elsewhere.get("/api/v1/auth/me").status_code == 401
    assert _login(client, PASSWORD).status_code == 401
    signed_in = _login(client, NEW_PASSWORD)
    assert signed_in.status_code == 200
    # Reading the inbox is what confirming an address means.
    assert signed_in.json()["user"]["email_verified"] is True


def test_a_reset_link_works_once(client: TestClient, outbox: RecordingMailer) -> None:
    register(client)
    outbox.sent.clear()
    _forgot(client, "kari@example.no")
    token = _token_in(outbox.only.text, "/nytt-passord/")

    assert _reset(client, token).status_code == 204
    replayed = _reset(client, token, "nok-et-tredje-passord")

    assert replayed.status_code == 400
    assert replayed.json()["code"] == "invalid_email_token"
    assert _login(client, NEW_PASSWORD).status_code == 200


def test_a_reset_link_expires_within_the_hour(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    register(client)
    outbox.sent.clear()
    _forgot(client, "kari@example.no")
    token = _token_in(outbox.only.text, "/nytt-passord/")
    _later(monkeypatch, timedelta(minutes=31))

    assert _reset(client, token).status_code == 400
    monkeypatch.undo()
    assert _login(client, PASSWORD).status_code == 200


def test_a_newer_reset_link_kills_the_older_one(
    client: TestClient, outbox: RecordingMailer
) -> None:
    register(client)
    outbox.sent.clear()
    _forgot(client, "kari@example.no")
    _forgot(client, "kari@example.no")
    old = _token_in(outbox.sent[0].text, "/nytt-passord/")
    new = _token_in(outbox.sent[1].text, "/nytt-passord/")

    assert _reset(client, old).status_code == 400
    assert _reset(client, new).status_code == 204


def test_a_weak_password_does_not_spend_the_link(
    client: TestClient, outbox: RecordingMailer
) -> None:
    register(client)
    outbox.sent.clear()
    _forgot(client, "kari@example.no")
    token = _token_in(outbox.only.text, "/nytt-passord/")

    weak = _reset(client, token, "kort")
    assert weak.status_code == 422
    assert _reset(client, token).status_code == 204


def test_reset_mail_is_capped_per_hour_without_saying_so(
    client: TestClient, outbox: RecordingMailer
) -> None:
    """The cap holds, and the capped request looks like every other one."""
    register(client)
    outbox.sent.clear()

    answers = [_forgot(client, "kari@example.no") for _ in range(5)]

    assert {answer.status_code for answer in answers} == {202}
    assert len(outbox.sent) == 3


def test_a_reset_lifts_the_login_lockout(client: TestClient, outbox: RecordingMailer) -> None:
    """The lockout bounds guessing; the person who reads the inbox guessed nothing."""
    register(client)
    write(client, "POST", "/api/v1/auth/logout")
    for _ in range(3):  # LOGIN_MAX_FAILED_ATTEMPTS in conftest
        _login(client, "feil-passord-her")
    assert _login(client, PASSWORD).status_code == 429
    outbox.sent.clear()

    _forgot(client, "kari@example.no")
    assert _reset(client, _token_in(outbox.only.text, "/nytt-passord/")).status_code == 204

    assert _login(client, NEW_PASSWORD).status_code == 200


def test_a_deactivated_account_gets_no_reset_mail(
    client: TestClient, outbox: RecordingMailer, database_url: str
) -> None:
    register(client)
    outbox.sent.clear()
    engine = _sync_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE app_user SET is_active = 0"))
    engine.dispose()

    answer = _forgot(client, "kari@example.no")

    assert answer.status_code == 202
    assert outbox.sent == []


def test_the_link_from_a_changed_address_is_refused(
    client: TestClient, outbox: RecordingMailer, database_url: str
) -> None:
    """A link proves the inbox it went to, not whichever one the account has now."""
    register(client)
    outbox.sent.clear()
    _forgot(client, "kari@example.no")
    token = _token_in(outbox.only.text, "/nytt-passord/")
    engine = _sync_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE app_user SET email = 'kari@annet.no'"))
    engine.dispose()

    assert _reset(client, token).status_code == 400


# --- What the messages say --------------------------------------------------------


def test_the_messages_are_norwegian_plain_text_with_no_tracking() -> None:
    """Text first, the link in it, and nothing fetched when the message is opened."""
    deadline = utc_now() + timedelta(hours=48)
    messages = (
        verification_mail(
            to="kari@example.no",
            full_name="Kari <b>Nordmann</b>",
            url="https://app.example/bekreft-epost/#token=abc",
            expires_at=deadline,
        ),
        password_reset_mail(
            to="kari@example.no",
            full_name="Kari Nordmann",
            url="https://app.example/nytt-passord/#token=abc",
            minutes=30,
        ),
    )
    for message in messages:
        assert "Hei Kari," in message.text
        assert "#token=abc" in message.text
        assert message.html is not None
        # The only image is the inline mark; nothing is loaded from a server.
        assert 'src="http' not in message.html
        assert "<b>" not in message.html
        assert message.reply_to is None
    assert "30 minutter" in messages[1].text
    assert "Ba du ikke om dette" in messages[1].text


def test_the_bankid_messages_carry_a_link_only_where_one_is_meant() -> None:
    """A BankID account gets a password from one message and one only: the switch.

    The answer to a reset request and the notice after a switch to BankID point
    at a page and set nothing, because a password on an account BankID opened
    is what BankID's terms forbid (docs/integrations/bankid.md).
    """
    notice = bankid_sign_in_mail(
        to="kari@example.no",
        full_name="Kari <b>Nordmann</b>",
        url="https://app.example/logg-inn/",
    )
    switch = switch_to_email_mail(
        to="kari@example.no",
        full_name="Kari Nordmann",
        url="https://app.example/bytt-til-epost/#token=abc",
        minutes=30,
    )
    switched = switched_to_bankid_mail(
        to="kari@example.no", full_name="Kari Nordmann", url="https://app.example/konto/"
    )
    for message in (notice, switch, switched):
        assert "Hei Kari," in message.text
        assert message.html is not None
        assert 'src="http' not in message.html
        assert "<b>" not in message.html
        assert message.reply_to is None
    for informs in (notice, switched):
        assert "#token=" not in informs.text
        assert "#token=" not in (informs.html or "")
    assert "https://app.example/logg-inn/" in notice.text
    assert "#token=abc" in switch.text
    assert "30 minutter" in switch.text
    assert "slutter innlogging med BankID å virke" in switch.text
    assert "Ba du ikke om dette, ikke følg lenken" in switch.text


async def test_a_suppressed_recipient_is_not_a_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloudflare drops a suppressed address inside a 200. That is not delivered."""
    suppressed = httpx.Response(
        200,
        json={
            "success": True,
            "errors": [],
            "result": {
                "delivered": [],
                "permanent_bounces": [],
                "queued": [],
                "suppressed_recipients": ["kari@example.no"],
            },
        },
    )
    monkeypatch.setattr(
        mail_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: suppressed)),
    )
    mailer = CloudflareMailer(
        Settings(
            mail_from="no-reply@myntos.no",
            smtp_host=None,
            cloudflare_account_id="acc123",
            cloudflare_email_token="tok-secret",
        )
    )

    delivery = await mailer.send(
        password_reset_mail(
            to="kari@example.no", full_name=None, url="https://app.example/x", minutes=30
        )
    )

    assert delivery.sent is False
    assert delivery.error is not None
    assert "suppression" in delivery.error
