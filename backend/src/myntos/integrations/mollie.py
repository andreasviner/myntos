"""Mollie, which takes the payment for a KI plan.

The same provider and the same design as Løynd's own checkout
(``loynd-backend/app/mollie.py``), so one company runs one way of taking money:

1. The owner of a company picks a plan on ``/ki/``.
2. ``POST /api/v1/companies/{id}/ki/betaling`` creates a payment here and hands
   the browser Mollie's hosted ``checkout_url``. Card or account details are
   typed in on Mollie's page and never pass through Myntos.
3. The browser comes back to ``/ki/?betaling=<ref>``, and the page asks Myntos
   to settle it.
4. Mollie also POSTs ``id=tr_…`` to the webhook. That body is only a hint: the
   status and the amount are always fetched again from Mollie with the server's
   key, so a forged webhook can at most make Myntos ask Mollie a question.

Mollie B.V. is in Amsterdam and processes in the EEA, so the payer's name and
payment details it collects stay inside rule 5. Myntos sends it an amount, a
description naming the plan, and a reference of its own: no company name, no
organisation number, nothing from the books.

Amounts are :class:`~decimal.Decimal` here, as everywhere in Myntos. Mollie
takes and returns them as strings with two decimals, which is exactly a
Decimal's ``quantize`` and never a float's ``round``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx

from myntos.core.config import get_settings

logger = logging.getLogger(__name__)

MOLLIE_API_BASE = "https://api.mollie.com/v2"

#: Mollie's payment ids are ``tr_`` and letters and digits. Checked before an id
#: from a webhook body goes into a URL, because that body is anybody's to send.
PAYMENT_ID_RE = re.compile(r"^tr_[A-Za-z0-9]{1,64}$")

_CENT = Decimal("0.01")


def is_valid_payment_id(value: str | None) -> bool:
    return bool(value) and PAYMENT_ID_RE.fullmatch(value or "") is not None


class MollieError(Exception):
    """Mollie refused or did not answer. Never carries the key."""


class MollieNotConfiguredError(MollieError):
    """No key for the mode in use, or the mode is ``off``: nothing can be bought."""


class MolliePaymentNotFoundError(MollieError):
    """Mollie has no such payment for this key (wrong id, or the other mode's)."""


@dataclass(frozen=True)
class MolliePayment:
    """The part of Mollie's Payment resource Myntos acts on."""

    id: str
    mode: str
    # open, pending, authorized, paid, canceled, expired, failed. Only "paid"
    # opens a period; the rest are read so the page can say what happened.
    status: str
    amount: Decimal
    currency: str
    checkout_url: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def active_mode() -> Literal["test", "live"] | None:
    """``"test"`` or ``"live"`` when a payment can be taken, otherwise ``None``."""
    settings = get_settings()
    if settings.mollie_mode == "off":
        return None
    secret = (
        settings.mollie_live_key if settings.mollie_mode == "live" else settings.mollie_test_key
    )
    if secret is None or not secret.get_secret_value().strip():
        return None
    return settings.mollie_mode


def is_configured() -> bool:
    return active_mode() is not None


def _key() -> str:
    settings = get_settings()
    mode = active_mode()
    secret = settings.mollie_live_key if mode == "live" else settings.mollie_test_key
    if mode is None or secret is None:
        raise MollieNotConfiguredError("betaling er ikke satt opp")
    return secret.get_secret_value().strip()


def _amount_string(amount: Decimal) -> str:
    if amount <= 0:
        raise ValueError("a payment is for a positive amount")
    return str(amount.quantize(_CENT))


def _amount(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_CENT)
    except (InvalidOperation, ValueError) as error:
        logger.error("Mollie returned an unexpected payment amount", exc_info=error)
        raise MollieError("uventet beløp fra Mollie") from error


def is_public_https(url: str | None) -> bool:
    """Whether Mollie could reach ``url``: it refuses a payment whose webhook it cannot."""
    if not url:
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".local")


def _parse(data: object) -> MolliePayment:
    if not isinstance(data, dict):
        logger.error("Mollie returned an unexpected payment payload")
        raise MollieError("uventet svar fra Mollie")
    payment_id = str(data.get("id") or "")
    if not is_valid_payment_id(payment_id):
        logger.error("Mollie returned an invalid payment id")
        raise MollieError("uventet betalings-id fra Mollie")
    amount = data.get("amount")
    amount = amount if isinstance(amount, dict) else {}
    links = data.get("_links")
    checkout = links.get("checkout") if isinstance(links, dict) else None
    href = checkout.get("href") if isinstance(checkout, dict) else None
    metadata = data.get("metadata")
    return MolliePayment(
        id=payment_id,
        mode=str(data.get("mode") or ""),
        status=str(data.get("status") or ""),
        amount=_amount(amount.get("value")),
        currency=str(amount.get("currency") or "").upper(),
        checkout_url=str(href) if isinstance(href, str) and href else None,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


async def _request(method: str, path: str, *, json: dict[str, Any] | None = None) -> object:
    key = _key()
    timeout = httpx.Timeout(get_settings().mollie_timeout_seconds, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{MOLLIE_API_BASE}{path}",
                json=json,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
    except httpx.HTTPError as error:
        logger.error("Mollie could not be reached: %s", type(error).__name__, exc_info=error)
        raise MollieError("fikk ikke kontakt med Mollie") from error
    if response.status_code == httpx.codes.NOT_FOUND:
        raise MolliePaymentNotFoundError("Mollie kjenner ikke betalingen")
    if response.is_error:
        # The status only. Mollie's error body can quote the request back, and
        # a 401 body is about the key.
        logger.error(
            "Mollie answered %s to %s %s", response.status_code, method, path.split("/")[1]
        )
        raise MollieError("Mollie avviste forespørselen")
    try:
        return response.json()
    except ValueError as error:
        logger.error(
            "Mollie returned unreadable JSON for %s %s", method, path.split("/")[1], exc_info=error
        )
        raise MollieError("uleselig svar fra Mollie") from error


async def create_payment(
    *,
    amount: Decimal,
    description: str,
    redirect_url: str,
    metadata: dict[str, str],
    currency: str = "NOK",
) -> MolliePayment:
    """Start a payment and return it, with the ``checkout_url`` to send the browser to."""
    payload: dict[str, Any] = {
        "amount": {"currency": currency, "value": _amount_string(amount)},
        "description": description[:255],
        "redirectUrl": redirect_url,
        "metadata": metadata,
        "locale": "nb_NO",
    }
    webhook = get_settings().mollie_webhook_url
    if is_public_https(webhook):
        payload["webhookUrl"] = (webhook or "").strip()
    return _parse(await _request("POST", "/payments", json=payload))


async def get_payment(payment_id: str) -> MolliePayment:
    """Mollie's own word on a payment: the only status Myntos believes."""
    if not is_valid_payment_id(payment_id):
        raise MolliePaymentNotFoundError("ugyldig betalings-id")
    return _parse(await _request("GET", f"/payments/{quote(payment_id, safe='')}"))
