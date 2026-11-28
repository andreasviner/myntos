"""What a company can buy, and what it gets for it.

A table in code rather than in the database, for the same reason as the
category catalogue: a price is a promise printed on ``/priser/``, and a promise
should change in a reviewed commit, not in a row somebody edited. A subscription
or period copies the plan's price and NOK value allowance onto its own row, so
changing a plan here never changes what a company already paid for in a running
period.

**Value-based quotas (NOK internally, % for the user).** Paid levels («Medium»
and «Smart») run on different models with very different per-token costs, and
Løynd's live tiers update automatically as newer models arrive. Bounding weekly
limits by raw token counts would either under-serve «Medium» or under-price
«Smart». Instead, every plan carries a weekly value budget in NOK (ex. VAT,
roughly 75 % of the plan's weekly price excl. VAT), paid requests deduct their
live NOK cost from Løynd's ``/v1/models`` pricing, and the UI and API report
only the remaining percentage (0 to 100 %).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: Merverdiavgift on a service sold in Norway, the general rate. Myntos sells
#: to Norwegian companies only, so there is no other rate to choose between.
VAT_RATE = Decimal("0.25")
CURRENCY = "NOK"
_ORE = Decimal("0.01")


@dataclass(frozen=True)
class Plan:
    """One plan as the page shows it and the invoice or checkout charges it."""

    code: str
    #: Bokmål, shown on /ki/about/ and /priser/ and on the invoice line.
    name: str
    #: Kroner per month, excluding merverdiavgift, as /priser/ prints it for a
    #: company that deducts the VAT.
    price_excl_vat: Decimal
    #: Weekly paid-KI value budget in NOK (ex. VAT), backend-only. Resets every
    #: Monday 00:00 Oslo time; never exposed directly to users (UI shows %).
    weekly_value_nok: Decimal
    #: Relative tier label printed on /priser/ and /ki/about/ (no token counts).
    quota_label: str
    #: One line for the plan's card.
    summary: str

    @property
    def monthly_value_nok(self) -> Decimal:
        """Total value budget for a legacy one-off paid month (4 weeks)."""
        return (self.weekly_value_nok * Decimal(4)).quantize(_ORE)

    @property
    def vat(self) -> Decimal:
        return (self.price_excl_vat * VAT_RATE).quantize(_ORE)

    @property
    def price_incl_vat(self) -> Decimal:
        """What is charged with 25 % Norwegian VAT."""
        return self.price_excl_vat + self.vat


PLANS: tuple[Plan, ...] = (
    Plan(
        code="start",
        name="KI Start",
        price_excl_vat=Decimal("49.00"),
        weekly_value_nok=Decimal("9.00"),
        quota_label="Standard ukekvote",
        summary="Nok til omtrent hundre bilag i måneden med «Smart».",
    ),
    Plan(
        code="pluss",
        name="KI Pluss",
        price_excl_vat=Decimal("149.00"),
        weekly_value_nok=Decimal("28.00"),
        quota_label="3× ukekvote",  # noqa: RUF001
        summary="For et selskap med jevn drift og mange bilag.",
    ),
    Plan(
        code="pro",
        name="KI Pro",
        price_excl_vat=Decimal("399.00"),
        weekly_value_nok=Decimal("75.00"),
        quota_label="8× ukekvote",  # noqa: RUF001
        summary="For regnskapsførere og selskaper med mye å føre.",
    ),
)

_BY_CODE = {plan.code: plan for plan in PLANS}


def by_code(code: str) -> Plan | None:
    return _BY_CODE.get(code)
