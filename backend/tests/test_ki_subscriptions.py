"""Monthly KI subscriptions, weekly NOK-backed percentage quotas, and section 5-1 invoicing.

Covers the complete lifecycle:
- Owner activates a monthly KI plan without needing Mollie.
- Myntos issues a real section 5-1 SalesDocument from the configured billing
  company (gapless numbering, double-entry voucher in SALG, KID, EHF XML, PDF).
- Delivery via EHF over Peppol when buyer can receive EHF, and via email with
  attached PDF (captured in the test outbox), plus PDF download in Myntos.
- Weekly NOK value budgets (Start 9 kr, Pluss 28 kr, Pro 75 kr) exposed strictly
  as remaining percentage (0 to 100 %), enforced on paid levels and resetting
  Monday 00:00 Europe/Oslo, while «Gratis» stays unlimited.
- Plan switching mid-month (no duplicate invoice, higher remaining % at once),
  canceling at period end, monthly renewal sweep, and company closure.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from myntos.api.deps import get_document_storage, get_mailer
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.config import get_settings
from myntos.domain.ki import invoicing as ki_invoicing
from myntos.domain.ki import service as ki_service
from myntos.domain.ki.models import KiSubscription, KiSubscriptionStatus
from myntos.domain.peppol.models import PeppolRegistration
from myntos.integrations import ademico, loynd, peppol_smp
from myntos.integrations.loynd import Level
from tests.conftest import PASSWORD, RecordingMailer, join_company, register, write
from tests.test_cashflow_api import create_company
from tests.test_ki_levels import LINE, Wire, _answer, install_wire


@pytest.fixture
def loynd_wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


SELLER_ORGNR = "915933149"
BUYER_ORGNR = "923609016"


@pytest.fixture
def billing_setup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, outbox: RecordingMailer
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Create a seller (billing) company and a buyer company, and enable KI billing."""
    session = register(client)
    outbox.sent.clear()
    org_id = session["memberships"][0]["organization_id"]
    seller = create_company(client, org_id, orgnr=SELLER_ORGNR, name="Lindeman Technologies AS")
    # Ensure seller has full § 5-1 fields (address + Foretaksregisteret + bank account + VAT).
    updated = write(
        client,
        "PATCH",
        f"/api/v1/companies/{seller['id']}",
        json={
            "address_line": "Storgata 1",
            "postal_code": "0155",
            "city": "Oslo",
            "vat_registered": True,
            "in_foretaksregisteret": True,
        },
    )
    assert updated.status_code == 200, updated.text
    bank = write(
        client,
        "POST",
        f"/api/v1/companies/{seller['id']}/bankkontoer",
        json={"account": "15062033158", "label": "Driftskonto"},
    )
    assert bank.status_code == 201, bank.text
    buyer = create_company(
        client,
        seller["organization_id"],
        orgnr=BUYER_ORGNR,
        name="Nordlys Verksted AS",
    )

    monkeypatch.setenv("KI_BILLING_COMPANY_ID", str(seller["id"]))
    monkeypatch.setattr(loynd, "_key", lambda: "loynd_sk_test")
    get_settings.cache_clear()
    yield seller, buyer
    get_settings.cache_clear()


def _ki(client: TestClient, company_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/ki")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_owner_activates_monthly_subscription_and_gets_section_5_1_invoice_and_pdf_mail(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
    outbox: RecordingMailer,
) -> None:
    seller, buyer = billing_setup

    status_before = _ki(client, buyer["id"])
    assert status_before["subscription_available"] is True
    assert status_before["can_manage_subscription"] is True
    assert status_before["subscription"] is None
    assert status_before["weekly"] is None

    activated = write(
        client,
        "PUT",
        f"/api/v1/companies/{buyer['id']}/ki/abonnement",
        json={"plan": "pluss", "billing_email": "faktura@nordlys.no"},
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["subscription"]["plan"] == "pluss"
    assert body["subscription"]["status"] == "active"
    assert body["subscription"]["quota_label"] == "3× ukekvote"  # noqa: RUF001
    assert "weekly_tokens_allowance" not in body["subscription"]
    assert body["weekly"]["remaining_percent"] == 100
    assert body["weekly"]["used_percent"] == 0
    assert "tokens_allowance" not in body["weekly"]
    assert "tokens_used" not in body["weekly"]
    assert "tokens_left" not in body["weekly"]

    invoice = body["invoice"]
    assert invoice is not None
    assert invoice["plan"] == "pluss"
    assert invoice["amount_excl_vat"] == "149.00"
    assert invoice["vat_amount"] == "37.25"
    assert invoice["amount_incl_vat"] == "186.25"
    assert invoice["invoice_number"] is not None
    assert invoice["delivery_method"] == "email"
    assert invoice["delivery_status"] == "sent"
    assert invoice["has_pdf"] is True

    # Real section 5-1 SalesDocument was issued in seller's own books.
    seller_docs = client.get(f"/api/v1/companies/{seller['id']}/faktura").json()
    assert len(seller_docs) == 1
    assert seller_docs[0]["document_number"] == invoice["invoice_number"]
    assert seller_docs[0]["buyer_name"] == "Nordlys Verksted AS"
    assert seller_docs[0]["status"] == "utstedt"
    assert seller_docs[0]["gross_total"] == "186.25"

    # Email with attached PDF went into outbox.
    sent_mail = outbox.only
    assert sent_mail.to == "faktura@nordlys.no"
    assert invoice["invoice_number"] in sent_mail.subject
    assert len(sent_mail.attachments) == 1
    assert sent_mail.attachments[0].subtype == "pdf"
    assert sent_mail.attachments[0].data.startswith(b"%PDF-")

    # Buyer can list and download the PDF in Myntos.
    listed = client.get(f"/api/v1/companies/{buyer['id']}/ki/fakturaer")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [invoice["id"]]

    pdf = client.get(f"/api/v1/companies/{buyer['id']}/ki/fakturaer/{invoice['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")

    # Paid levels are now available on GET /ki.
    status_after = _ki(client, buyer["id"])
    assert [row["available"] for row in status_after["levels"]] == [True, True, True]


def test_monthly_invoice_delivers_via_ehf_over_peppol_when_buyer_can_receive(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    outbox: RecordingMailer,
) -> None:
    seller, buyer = billing_setup

    # Register seller on Peppol directly in the test DB.
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]

    async def register_seller() -> None:
        async with sessions() as db:
            db.add(
                PeppolRegistration(
                    company_id=seller["id"],
                    remote_company_id="le-seller-1",
                    orgnr=SELLER_ORGNR,
                    verified_at=datetime.now(UTC),
                )
            )
            await db.commit()

    assert client.portal is not None
    client.portal.call(register_seller)

    ehf_sent: list[str] = []

    async def fake_can_receive(orgnr: str, *, credit_note: bool) -> bool:
        assert orgnr == BUYER_ORGNR
        assert credit_note is False
        return True

    async def fake_send(*, xml: str, filename: str) -> ademico.Sent:
        ehf_sent.append(filename)
        assert BUYER_ORGNR in xml
        return ademico.Sent(
            id="doc-ehf-1",
            delivery=ademico.Delivery(
                status="delivered", failure_category=None, failure_message=None
            ),
        )

    monkeypatch.setattr(ademico, "is_configured", lambda: True)
    monkeypatch.setattr(peppol_smp, "can_receive", fake_can_receive)
    monkeypatch.setattr(ademico, "send", fake_send)

    activated = write(
        client,
        "PUT",
        f"/api/v1/companies/{buyer['id']}/ki/abonnement",
        json={"plan": "start", "billing_email": "ehf-kunde@nordlys.no"},
    )
    assert activated.status_code == 200, activated.text
    invoice = activated.json()["invoice"]
    assert invoice["delivery_method"] == "ehf"
    assert invoice["delivery_status"] == "delivered"
    assert len(ehf_sent) == 1
    # Email PDF copy still sent too, noting EHF was sent.
    assert "EHF" in outbox.only.text


def test_switching_plan_mid_month_updates_weekly_quota_without_double_invoicing(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
    loynd_wire: Wire,
) -> None:
    _, buyer = billing_setup
    # medium NOK rate: 1.38/M in, 6.44/M out -> 1M in + 500k out = 4.60 NOK.
    loynd_wire.answers["medium"] = _answer(LINE, prompt_tokens=1_000_000, completion_tokens=500_000)

    first = write(
        client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
    )
    assert first.status_code == 200
    assert first.json()["invoice"] is not None
    assert first.json()["subscription"]["quota_label"] == "Standard ukekvote"
    assert first.json()["weekly"]["remaining_percent"] == 100

    # Spend 4.60 NOK of Start's 9.00 NOK weekly budget -> 49 % remaining.
    call = write(
        client,
        "POST",
        f"/api/v1/companies/{buyer['id']}/faktura/linjeforslag",
        json={"text": "10 timer rådgivning", "level": "medium"},
    )
    assert call.status_code == 200
    assert _ki(client, buyer["id"])["weekly"]["remaining_percent"] == 49

    # Switching to Pro (75.00 NOK budget) raises remaining % at once with no second invoice.
    switched = write(
        client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "pro"}
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["invoice"] is None
    assert switched.json()["subscription"]["plan"] == "pro"
    assert switched.json()["subscription"]["quota_label"] == "8× ukekvote"  # noqa: RUF001
    # 70.40 / 75.00 = 93.866... % -> 94 % remaining.
    assert switched.json()["weekly"]["remaining_percent"] == 94
    assert switched.json()["weekly"]["used_percent"] == 6

    invoices = client.get(f"/api/v1/companies/{buyer['id']}/ki/fakturaer").json()
    assert len(invoices) == 1


def test_weekly_value_limit_enforced_and_resets_on_monday(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
    loynd_wire: Wire,
) -> None:
    _, buyer = billing_setup
    # high NOK rate: 8.625/M in, 50.60/M out -> 200k in + 150k out = 9.315 NOK (> 9.00 budget).
    loynd_wire.answers["high"] = _answer(LINE, prompt_tokens=200_000, completion_tokens=150_000)

    # Activate KI Start (9.00 NOK / week internal budget).
    resp = write(
        client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
    )
    assert resp.status_code == 200

    # First paid call succeeds and overshoots 9.00 NOK (9.315 NOK used).
    first = write(
        client,
        "POST",
        f"/api/v1/companies/{buyer['id']}/faktura/linjeforslag",
        json={"text": "10 timer rådgivning", "level": "thinking"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["ki_level"] == "thinking"
    assert first.json()["warnings"] == []

    ki = _ki(client, buyer["id"])
    assert ki["weekly"]["remaining_percent"] == 0
    assert ki["weekly"]["used_percent"] == 100
    assert [row["available"] for row in ki["levels"]] == [True, False, False]

    # Second paid call in the same week falls back to free with weekly reset notice.
    second = write(
        client,
        "POST",
        f"/api/v1/companies/{buyer['id']}/faktura/linjeforslag",
        json={"text": "5 timer rådgivning", "level": "medium"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["ki_level"] == "free"
    assert "Ukekvoten i KI-planen er brukt opp" in second.json()["warnings"][0]
    assert "mandag" in second.json()["warnings"][0]
    assert "token" not in second.json()["warnings"][0].lower()

    # Next Monday in Oslo resets the weekly quota automatically.
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]

    async def check_next_week() -> ki_service.Resolution:
        async with sessions() as db:
            next_week = datetime.now(UTC) + timedelta(days=8)
            return await ki_service.resolve(db, buyer["id"], Level.MEDIUM, now=next_week)

    assert client.portal is not None
    next_res = client.portal.call(check_next_week)
    assert next_res.level is Level.MEDIUM
    assert next_res.notice is None


def test_monthly_renewal_issues_second_invoice_and_canceled_subscription_ends_cleanly(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    seller, buyer = billing_setup
    buyer2 = create_company(
        client, seller["organization_id"], orgnr="910000004", name="Avsluttes AS"
    )

    # Buyer 1 stays active; Buyer 2 activates then cancels at period end.
    assert (
        write(
            client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
        ).status_code
        == 200
    )
    assert (
        write(
            client, "PUT", f"/api/v1/companies/{buyer2['id']}/ki/abonnement", json={"plan": "start"}
        ).status_code
        == 200
    )
    canceled = write(client, "DELETE", f"/api/v1/companies/{buyer2['id']}/ki/abonnement")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceling"
    assert canceled.json()["cancel_at_period_end"] is True

    # Run renewal sweep 35 days in the future.
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]
    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]
    mailer = client.app.dependency_overrides[get_mailer]()  # type: ignore[attr-defined]
    future = datetime.now(UTC) + timedelta(days=35)

    assert client.portal is not None
    renewed, ended = client.portal.call(
        lambda: ki_invoicing.process_due_subscriptions(sessions, storage, mailer, now=future)
    )
    assert renewed == 1
    assert ended == 1

    # Buyer 1 got its second monthly invoice; Buyer 2 got only its first and is now canceled.
    assert len(client.get(f"/api/v1/companies/{buyer['id']}/ki/fakturaer").json()) == 2
    assert len(client.get(f"/api/v1/companies/{buyer2['id']}/ki/fakturaer").json()) == 1
    assert _ki(client, buyer2["id"])["subscription"] is None


def test_only_owner_can_manage_subscription_and_ki_off_refuses(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, buyer = billing_setup

    # KI off refuses subscription activation with 409.
    write(client, "PATCH", f"/api/v1/companies/{buyer['id']}", json={"ki_enabled": False})
    refused_off = write(
        client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
    )
    assert refused_off.status_code == 409
    assert refused_off.json()["code"] == "ki_turned_off"
    write(client, "PATCH", f"/api/v1/companies/{buyer['id']}", json={"ki_enabled": True})

    # Admin (not owner) gets 403.
    join_company(client, buyer["id"], email="admin@nordlys.no", role="admin")
    assert _ki(client, buyer["id"])["can_manage_subscription"] is False
    refused_role = write(
        client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
    )
    assert refused_role.status_code == 403


def test_closing_company_cancels_active_ki_subscription(
    client: TestClient,
    billing_setup: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    _, buyer = billing_setup
    # Book one voucher so closing keeps (soft-closes) the company instead of deleting immediately.
    posted = write(
        client,
        "POST",
        f"/api/v1/companies/{buyer['id']}/cashflow",
        json={
            "type_code": "varekjop",
            "occurred_on": "2026-03-10",
            "gross_amount": "500.00",
            "description": "Kontorrekvisita",
        },
    )
    assert posted.status_code == 201, posted.text
    assert (
        write(
            client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
        ).status_code
        == 200
    )

    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{buyer['id']}/sletting",
        json={"company_name": buyer["name"], "password": PASSWORD},
    )
    assert closed.status_code == 200, closed.text

    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]

    async def read_status() -> str:
        async with sessions() as db:
            sub = await db.scalar(
                select(KiSubscription).where(KiSubscription.company_id == buyer["id"])
            )
            assert sub is not None
            return str(sub.status)

    assert client.portal is not None
    assert client.portal.call(read_status) == KiSubscriptionStatus.CANCELED.value
