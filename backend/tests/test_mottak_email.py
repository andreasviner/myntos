"""End-to-end tests for Mottatt email invoice intake, address allocation, and sender security."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from myntos.api.deps import get_document_storage
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.db.session import get_session
from myntos.domain.identity.models import User
from myntos.domain.mottak.address import candidate_local_parts, extract_local_part
from tests.conftest import PASSWORD, agent_header, register, write


def create_company(
    client: TestClient, organization_id: int, *, orgnr: str, name: str
) -> dict[str, Any]:
    resp = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": utc_now().year,
            "address_line": "Kirkegata 2",
            "postal_code": "0153",
            "city": "Oslo",
            "vat_registered": True,
            "bank_account": "15062033158",
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


WEBHOOK_SECRET = "test-inbound-email-secret-0123456789abcdef"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
# Minimal valid 1x1 PNG bytes.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)
# Larger (> 4 KiB) PNG for testing standalone inline receipt photos.
RECEIPT_PNG_BYTES = PNG_BYTES + b"\x00" * 5000

ORG_A = "910000004"
ORG_B = "910000012"
ORG_C = "915933149"
ORG_D = "910000020"
THIS_YEAR = utc_now().year


@pytest.fixture
def inbound_configured() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("INBOUND_EMAIL_DOMAIN", "myntos.no")
        patch.setenv("INBOUND_EMAIL_WEBHOOK_SECRET", WEBHOOK_SECRET)
        patch.setenv("INBOUND_EMAIL_MAX_PER_HOUR", "60")
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


def _sign(
    raw_body: bytes, *, secret: str = WEBHOOK_SECRET, timestamp: int | None = None
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.".encode("ascii") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Myntos-Timestamp": str(ts),
        "X-Myntos-Signature": f"sha256={digest}",
    }


def _build_email(
    *,
    from_header: str = "Leverandør Faktura <faktura@leverandor.no>",
    to_header: str = "test@myntos.no",
    subject: str = "Faktura 4201",
    body_text: str = "Hei, vedlagt følger faktura for november.",
    body_html: str | None = None,
    reply_to: str | None = None,
    message_id: str | None = "<msg-1@leverandor.no>",
    auth_headers: list[str] | None = None,
    attachments: list[tuple[str, bytes, str, str, str | None]] | None = None,
) -> bytes:
    """Build raw RFC 5322 bytes.

    Each attachment tuple is ``(filename, content, maintype, subtype, content_id_or_None)``.
    When ``content_id`` is provided, the part is added as ``inline`` with a ``Content-ID``.
    """
    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = to_header
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    if message_id:
        msg["Message-ID"] = message_id
    for auth in auth_headers or []:
        msg["Authentication-Results"] = auth

    msg.set_content(body_text)
    if body_html is not None:
        msg.add_alternative(body_html, subtype="html")

    for filename, content, maintype, subtype, cid in attachments or []:
        if cid is not None:
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
                disposition="inline",
                cid=cid,
            )
        else:
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
    return msg.as_bytes()


def _post_webhook(
    client: TestClient,
    raw_email: bytes,
    *,
    envelope_from: str = "faktura@leverandor.no",
    envelope_to: str = "test@myntos.no",
    secret: str = WEBHOOK_SECRET,
    timestamp: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    headers = {
        "Content-Type": "message/rfc822",
        "X-Myntos-Envelope-From": envelope_from,
        "X-Myntos-Envelope-To": envelope_to,
        **_sign(raw_email, secret=secret, timestamp=timestamp),
        **(extra_headers or {}),
    }
    return client.post("/api/v1/webhooks/inbound-email", content=raw_email, headers=headers)


def _mark_user_verified(client: TestClient, email: str) -> None:
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def mark() -> None:
        sessions = override()
        session = await sessions.__anext__()
        await session.execute(
            update(User).where(User.email == email.lower()).values(email_verified_at=utc_now())
        )
        await session.commit()

    assert client.portal is not None
    client.portal.call(mark)


# --- 1. Pure address generation & collision rules -----------------------------------


def test_candidate_local_parts_norwegian_names_and_reserved_words() -> None:
    first_five = [
        p for _, p in zip(range(5), candidate_local_parts("Test AS", ORG_A), strict=False)
    ]
    assert first_five == ["test", "testas", "testas2", "testas3", "testas4"]

    nordic = [
        p for _, p in zip(range(3), candidate_local_parts("Ås & Ødegård ENK", ORG_B), strict=False)
    ]
    assert nordic == ["asodegard", "asodegardenk", "asodegardenk2"]

    # Reserved word "kontakt" must be skipped -> "kontaktas"
    reserved = next(candidate_local_parts("Kontakt AS", ORG_C))
    assert reserved == "kontaktas"

    # Subaddressing extracts base local-part cleanly
    assert extract_local_part("test+faktura@myntos.no", "myntos.no") == "test"
    assert extract_local_part("TEST@MYNTOS.NO", "myntos.no") == "test"
    assert extract_local_part("test@other.no", "myntos.no") is None


def test_company_registration_assigns_collision_free_myntos_addresses(client: TestClient) -> None:
    session = register(client, email="eier@example.no")
    org_id = session["memberships"][0]["organization_id"]

    c1 = create_company(client, org_id, orgnr=ORG_A, name="Test AS")
    c2 = create_company(client, org_id, orgnr=ORG_B, name="Test AS")
    c3 = create_company(client, org_id, orgnr=ORG_C, name="Test AS")
    c4 = create_company(client, org_id, orgnr=ORG_D, name="Kontakt AS")

    s1 = client.get(f"/api/v1/companies/{c1['id']}/mottak/epost-innstillinger").json()
    s2 = client.get(f"/api/v1/companies/{c2['id']}/mottak/epost-innstillinger").json()
    s3 = client.get(f"/api/v1/companies/{c3['id']}/mottak/epost-innstillinger").json()
    s4 = client.get(f"/api/v1/companies/{c4['id']}/mottak/epost-innstillinger").json()

    assert s1["address"] == "test@myntos.no"
    assert s2["address"] == "testas@myntos.no"
    assert s3["address"] == "testas2@myntos.no"
    assert s4["address"] == "kontaktas@myntos.no"
    assert s1["enabled"] is True
    assert s1["sender_policy"] == "alle_med_varsel"
    assert s1["allowed_senders"] == []


# --- 2. Webhook authentication, replay window, and rate limit -----------------------


def test_webhook_fails_closed_when_unconfigured(client: TestClient) -> None:
    raw = _build_email(attachments=[("faktura.pdf", PDF_BYTES, "application", "pdf", None)])
    resp = _post_webhook(client, raw)
    assert resp.status_code == 503
    assert resp.json()["code"] == "inbox_not_available"


def test_webhook_rejects_bad_or_expired_signatures(
    client: TestClient, inbound_configured: None
) -> None:
    raw = _build_email(attachments=[("faktura.pdf", PDF_BYTES, "application", "pdf", None)])

    bad_sig = _post_webhook(client, raw, secret="wrong-secret")
    assert bad_sig.status_code == 401
    assert bad_sig.json()["code"] == "invalid_webhook_signature"

    stale = _post_webhook(client, raw, timestamp=int(time.time()) - 600)
    assert stale.status_code == 401
    assert stale.json()["code"] == "invalid_webhook_signature"

    missing = client.post(
        "/api/v1/webhooks/inbound-email",
        content=raw,
        headers={"X-Myntos-Envelope-To": "test@myntos.no"},
    )
    assert missing.status_code == 401


def test_webhook_rate_limits_per_company(
    client: TestClient,
    inbound_configured: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INBOUND_EMAIL_MAX_PER_HOUR", "2")
    get_settings.cache_clear()

    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    create_company(client, org_id, orgnr=ORG_A, name="Test AS")

    for i in range(2):
        raw = _build_email(
            message_id=f"<rate-{i}@lev.no>",
            attachments=[("faktura.pdf", PDF_BYTES, "application", "pdf", None)],
        )
        ok = _post_webhook(client, raw)
        assert ok.status_code == 200, ok.text
        assert ok.json()["stored"] == 1

    third = _post_webhook(
        client,
        _build_email(
            message_id="<rate-3@lev.no>",
            attachments=[("faktura.pdf", PDF_BYTES, "application", "pdf", None)],
        ),
    )
    assert third.status_code == 429
    assert third.json()["code"] == "inbound_email_rate_limited"
    assert "Retry-After" in third.headers


# --- 3. MIME parsing, filtering, dedup, inline logos & outermost Auth-Results -------


def test_mime_parsing_filters_unsafe_attachments_dedups_and_ignores_inline_logos(
    client: TestClient, inbound_configured: None
) -> None:
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    company = create_company(client, org_id, orgnr=ORG_A, name="Test AS")

    raw = _build_email(
        to_header="test+faktura@myntos.no",
        subject="Faktura + kvittering",
        body_text="Hei!\nVedlagt følger to bilag.",
        body_html="<html><body><script>alert(1)</script><p>HTML</p></body></html>",
        auth_headers=[
            # Outermost header added by Cloudflare edge (must win!):
            "mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass",
            # Inner header spoofed or from earlier hop:
            "evil.example; spf=fail; dkim=fail; dmarc=fail",
        ],
        attachments=[
            ("faktura.pdf", PDF_BYTES, "application", "pdf", None),
            ("kvittering.png", PNG_BYTES, "image", "png", None),
            ("virus.exe", b"MZ\x90\x00not-a-pdf", "application", "octet-stream", None),
            ("phish.html", b"<html>bad</html>", "text", "html", None),
            ("logo.png", PNG_BYTES, "image", "png", "<logo@leverandor.no>"),
        ],
    )
    res = _post_webhook(client, raw, envelope_to="test+faktura@myntos.no")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "accepted"
    assert body["stored"] == 2
    assert body["skipped_attachments"] == 2  # virus.exe + phish.html (inline cid logo skipped)

    # Replaying identical Message-ID is idempotent
    replay = _post_webhook(client, raw, envelope_to="test+faktura@myntos.no")
    assert replay.status_code == 200
    assert replay.json()["stored"] == 0

    listed = client.get(f"/api/v1/companies/{company['id']}/mottak/epost").json()
    assert len(listed) == 2
    assert {item["filename"] for item in listed} == {"faktura.pdf", "kvittering.png"}
    assert all(item["spf_result"] == "pass" for item in listed)
    assert all(item["dkim_result"] == "pass" for item in listed)
    assert all(item["dmarc_result"] == "pass" for item in listed)

    detail = client.get(f"/api/v1/companies/{company['id']}/mottak/epost/{listed[0]['id']}").json()
    assert "vedlagt følger to bilag" in detail["body_excerpt"].lower()
    assert "<script>" not in (detail["body_excerpt"] or "")

    # Standalone inline phone receipt (> 4 KiB, no explicit PDF attachment) IS accepted
    phone_mail = _build_email(
        message_id="<phone-receipt@example.no>",
        subject="Kvittering fra telefon",
        attachments=[("kvittering.png", RECEIPT_PNG_BYTES, "image", "png", "<photo@phone>")],
    )
    phone_res = _post_webhook(client, phone_mail)
    assert phone_res.status_code == 200
    assert phone_res.json()["stored"] == 1


# --- 4. Sender security, trust classification & strict allowlist policy -------------


def test_sender_trust_levels_spoof_warnings_and_strict_policy(
    client: TestClient, inbound_configured: None
) -> None:
    session = register(client, email="eier@firma.no")
    _mark_user_verified(client, "eier@firma.no")
    org_id = session["memberships"][0]["organization_id"]
    company = create_company(client, org_id, orgnr=ORG_A, name="Test AS")
    cid = company["id"]

    # 1) Verified company member -> egen_bruker
    member_mail = _build_email(
        from_header="Eier <eier@firma.no>",
        message_id="<m-member@firma.no>",
        auth_headers=["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass"],
        attachments=[("kvittering.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    assert _post_webhook(client, member_mail, envelope_from="eier@firma.no").json()["stored"] == 1

    # 2) Unknown external sender -> ukjent
    unknown_mail = _build_email(
        from_header="Telenor <faktura@telenor.no>",
        message_id="<m-unknown@telenor.no>",
        auth_headers=["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass"],
        attachments=[("telenor.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    assert (
        _post_webhook(client, unknown_mail, envelope_from="bounce@sendgrid.net").json()["stored"]
        == 1
    )

    # 3) Spoofed email (DMARC fail + Reply-To mismatch; outermost edge header
    # fails even though an inner header claims pass)
    spoof_mail = _build_email(
        from_header="Eier <eier@firma.no>",
        reply_to="svindel@evil.example",
        message_id="<m-spoof@evil.example>",
        auth_headers=[
            "mx.cloudflare.net; spf=fail; dkim=fail; dmarc=fail",
            "spoofed.mx; spf=pass; dkim=pass; dmarc=pass",
        ],
        attachments=[("haster.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    assert _post_webhook(client, spoof_mail, envelope_from="bot@evil.example").json()["stored"] == 1

    items = {
        item["filename"]: item
        for item in client.get(f"/api/v1/companies/{cid}/mottak/epost").json()
    }
    assert items["kvittering.pdf"]["sender_trust"] == "egen_bruker"
    assert items["kvittering.pdf"]["security_warnings"] == []

    # SendGrid envelope with passing DMARC does NOT falsely alarm; only ukjent
    assert items["telenor.pdf"]["sender_trust"] == "ukjent"
    assert len(items["telenor.pdf"]["security_warnings"]) == 1

    # Spoofed mail escalates to advarsel even though From claims to be the verified owner!
    assert items["haster.pdf"]["sender_trust"] == "advarsel"
    assert items["haster.pdf"]["dmarc_result"] == "fail"
    assert any("DMARC" in w for w in items["haster.pdf"]["security_warnings"])
    assert any("Reply-To" in w for w in items["haster.pdf"]["security_warnings"])

    # Approve @telenor.no and switch to strict policy (kun_godkjente)
    bad_allow = write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/mottak/epost-innstillinger",
        json={"allowed_senders": ["not-an-email"]},
    )
    assert bad_allow.status_code == 422
    assert bad_allow.json()["code"] == "invalid_allowed_sender"

    updated = write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/mottak/epost-innstillinger",
        json={
            "sender_policy": "kun_godkjente",
            "allowed_senders": ["@Telenor.NO", "faktura@posten.no"],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["allowed_senders"] == ["@telenor.no", "faktura@posten.no"]
    assert updated.json()["member_emails"] == ["eier@firma.no"]

    # Telenor invoice now immediately upgrades to godkjent with zero warnings!
    refreshed = {
        item["filename"]: item
        for item in client.get(f"/api/v1/companies/{cid}/mottak/epost").json()
    }
    assert refreshed["telenor.pdf"]["sender_trust"] == "godkjent"
    assert refreshed["telenor.pdf"]["security_warnings"] == []

    # Under kun_godkjente: stranger is rejected before storage
    stranger = _build_email(
        from_header="Fremmed <spam@stranger.example>",
        message_id="<m-stranger@stranger.example>",
        auth_headers=["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass"],
        attachments=[("spam.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    rej = _post_webhook(client, stranger, envelope_from="spam@stranger.example")
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
    assert rej.json()["reason"] == "sender_not_allowed"

    # Under kun_godkjente: spoofed email claiming to be @telenor.no with DMARC fail is also rejected
    spoof_approved = _build_email(
        from_header="faktura@telenor.no",
        message_id="<m-spoof-telenor@evil.example>",
        auth_headers=["mx.cloudflare.net; spf=fail; dkim=fail; dmarc=fail"],
        attachments=[("fake.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    rej2 = _post_webhook(client, spoof_approved, envelope_from="evil@evil.example")
    assert rej2.status_code == 200
    assert rej2.json()["status"] == "rejected"
    assert rej2.json()["reason"] == "sender_not_allowed"

    # Approved sender with passing auth is accepted
    ok_posten = _build_email(
        from_header="faktura@posten.no",
        message_id="<m-posten@posten.no>",
        auth_headers=["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass"],
        attachments=[("posten.pdf", PDF_BYTES, "application", "pdf", None)],
    )
    acc = _post_webhook(client, ok_posten, envelope_from="faktura@posten.no")
    assert acc.status_code == 200
    assert acc.json()["status"] == "accepted"
    assert acc.json()["stored"] == 1


# --- 5. Booking, bilag archive attachment, dismiss, reopen & agent keys -------------


def test_book_dismiss_reopen_and_agent_key_approval(
    client: TestClient, inbound_configured: None
) -> None:
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    company = create_company(client, org_id, orgnr=ORG_A, name="Test AS")
    cid = company["id"]

    raw = _build_email(
        subject="Lisens november",
        attachments=[
            ("lisens-1.pdf", PDF_BYTES, "application", "pdf", None),
            ("lisens-2.pdf", PDF_BYTES, "application", "pdf", None),
        ],
    )
    assert _post_webhook(client, raw).json()["stored"] == 2
    items = client.get(f"/api/v1/companies/{cid}/mottak/epost?status=ny").json()
    assert len(items) == 2
    first_id = items[0]["id"]
    second_id = items[1]["id"]

    # Download attachment bytes with nosniff header
    dl = client.get(f"/api/v1/companies/{cid}/mottak/epost/{first_id}/fil")
    assert dl.status_code == 200
    assert dl.content == PDF_BYTES
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["x-content-type-options"] == "nosniff"

    # Dismiss -> refuse booking while dismissed -> reopen -> book
    dismissed = write(client, "POST", f"/api/v1/companies/{cid}/mottak/epost/{first_id}/avvis")
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "avvist"

    blocked = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/mottak/epost/{first_id}/bokfor",
        json={
            "type_code": "datakostnad",
            "occurred_on": f"{THIS_YEAR}-11-20",
            "gross_amount": "1250.00",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "mail_document_dismissed"

    reopened = write(client, "POST", f"/api/v1/companies/{cid}/mottak/epost/{first_id}/gjenapne")
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "ny"

    booked = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/mottak/epost/{first_id}/bokfor",
        json={
            "type_code": "datakostnad",
            "occurred_on": f"{THIS_YEAR}-11-20",
            "gross_amount": "1250.00",
            "description": "Sky-lisens november",
        },
    )
    assert booked.status_code == 201, booked.text
    booked_body = booked.json()
    assert booked_body["status"] == "bokfort"
    voucher_id = booked_body["voucher_id"]
    assert voucher_id is not None

    # Voucher carries the archived PDF bilag
    vouchers = client.get(f"/api/v1/companies/{cid}/cashflow").json()
    posted = next(v for v in vouchers if v["id"] == voucher_id)
    assert len(posted["attachments"]) == 1
    att_id = posted["attachments"][0]["id"]
    att_dl = client.get(f"/api/v1/companies/{cid}/dokument/{att_id}")
    assert att_dl.status_code == 200
    assert att_dl.content == PDF_BYTES

    # Double booking refused
    again = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/mottak/epost/{first_id}/bokfor",
        json={
            "type_code": "datakostnad",
            "occurred_on": f"{THIS_YEAR}-11-20",
            "gross_amount": "1250.00",
        },
    )
    assert again.status_code == 409
    assert again.json()["code"] == "mail_document_already_booked"

    # Agent key with approval-gated post: can read, cannot edit settings, held for approval on book
    key_headers = agent_header(client, cid, scopes=("read",), approval_scopes=("write", "post"))
    assert (
        client.get(
            f"/api/v1/companies/{cid}/mottak/epost-innstillinger", headers=key_headers
        ).status_code
        == 200
    )
    refused_settings = client.put(
        f"/api/v1/companies/{cid}/mottak/epost-innstillinger",
        json={"enabled": False},
        headers=key_headers,
    )
    assert refused_settings.status_code == 403
    assert refused_settings.json()["code"] == "not_for_agent_keys"

    held = client.post(
        f"/api/v1/companies/{cid}/mottak/epost/{second_id}/bokfor",
        json={
            "type_code": "datakostnad",
            "occurred_on": f"{THIS_YEAR}-11-21",
            "gross_amount": "500.00",
        },
        headers={**key_headers, "Myntos-Note": "Book received email invoice"},
    )
    assert held.status_code == 202, held.text
    approval_id = held.json()["approval_id"]

    approval_view = client.get(f"/api/v1/companies/{cid}/godkjenninger/{approval_id}").json()
    assert any("E-postfaktura" in line for line in approval_view["target"])

    approved = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/godkjenninger/{approval_id}/godkjenn",
        json={"files": []},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"

    second_after = client.get(f"/api/v1/companies/{cid}/mottak/epost/{second_id}").json()
    assert second_after["status"] == "bokfort"
    assert second_after["voucher_id"] is not None


# --- 6. Company closure, retention & storage cleanup --------------------------------


def test_closure_disables_inbox_and_keeps_books_with_received_mail(
    client: TestClient, inbound_configured: None
) -> None:
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]

    # Empty company (has auto-allocated inbox address only) deletes immediately
    empty_company = create_company(client, org_id, orgnr=ORG_B, name="Tomt Selskap AS")
    deleted = write(
        client,
        "POST",
        f"/api/v1/companies/{empty_company['id']}/sletting",
        json={"company_name": "Tomt Selskap AS", "password": PASSWORD},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["outcome"] == "deleted"

    # Company with an unbooked email invoice must be kept for statutory retention
    company = create_company(client, org_id, orgnr=ORG_A, name="Test AS")
    raw = _build_email(attachments=[("bilag.pdf", PDF_BYTES, "application", "pdf", None)])
    assert _post_webhook(client, raw).json()["stored"] == 1

    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/sletting",
        json={"company_name": "Test AS", "password": PASSWORD},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["outcome"] == "kept"

    # Once closed, incoming mail to test@myntos.no is rejected
    after_close = _post_webhook(
        client,
        _build_email(
            message_id="<after-close@lev.no>",
            attachments=[("sent.pdf", PDF_BYTES, "application", "pdf", None)],
        ),
    )
    assert after_close.status_code == 200
    assert after_close.json()["status"] == "rejected"
    assert after_close.json()["reason"] == "inbox_disabled"

    # Verify purge_expired deletes the stored email attachment file after retention expires
    from datetime import date

    from sqlalchemy import select

    from myntos.domain.identity import closure
    from myntos.domain.mottak.models import IncomingMailDocument
    from myntos.storage.base import ObjectNotFoundError

    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]

    async def purge_and_check() -> None:
        sessions = override()
        db = await sessions.__anext__()
        key = await db.scalar(
            select(IncomingMailDocument.storage_key).where(
                IncomingMailDocument.company_id == company["id"]
            )
        )
        assert key is not None
        assert await storage.load(key) == PDF_BYTES
        purged = await closure.purge_expired(db, storage, today=date(THIS_YEAR + 10, 1, 1))
        assert company["id"] in purged
        with pytest.raises(ObjectNotFoundError):
            await storage.load(key)

    assert client.portal is not None
    client.portal.call(purge_and_check)
