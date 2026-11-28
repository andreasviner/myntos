"""The KI plans: their arithmetic, and the copy the prerendered pages print.

/priser/ and /ki/ are built ahead of time, so the prices they show are a
copy in ``frontend/src/data/kiplaner.ts``, not an answer from the API. A copy
is a promise that can drift from what the card is charged; this file is what
stops it.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from myntos.domain.ki.plans import PLANS, VAT_RATE, by_code

_REPO = Path(__file__).resolve().parents[2]
_PRINTED = _REPO / "frontend" / "src" / "data" / "kiplaner.ts"
#: What an answer engine quotes when asked what Myntos costs. Prose, so only
#: the cheapest price is checked: it is the number in the sentence.
_LLMS_TXT = _REPO / "docs" / "agents" / "llms.txt"

_ENTRY = re.compile(
    r"\{\s*code:\s*'(?P<code>[^']+)',\s*"
    r"name:\s*'(?P<name>[^']+)',\s*"
    r"priceExclVat:\s*'(?P<excl>[^']+)',\s*"
    r"priceInclVat:\s*'(?P<incl>[^']+)',\s*"
    r"quotaLabel:\s*'(?P<quota_label>[^']+)',\s*"
    r"summary:\s*'(?P<summary>[^']+)',\s*\}",
)


def _printed() -> list[dict[str, str]]:
    source = _PRINTED.read_text(encoding="utf-8")
    return [match.groupdict() for match in _ENTRY.finditer(source)]


def test_the_pages_print_every_plan_the_checkout_sells_in_the_same_order() -> None:
    printed = _printed()
    assert [entry["code"] for entry in printed] == [plan.code for plan in PLANS]


def test_the_printed_price_name_quota_label_and_line_are_the_backends() -> None:
    for entry in _printed():
        plan = by_code(entry["code"])
        assert plan is not None
        assert entry["name"] == plan.name
        assert Decimal(entry["excl"]) == plan.price_excl_vat
        assert Decimal(entry["incl"]) == plan.price_incl_vat
        assert entry["quota_label"] == plan.quota_label
        assert entry["summary"] == plan.summary


def test_what_is_paid_is_the_price_with_vat_in_whole_ore() -> None:
    for plan in PLANS:
        assert plan.vat == (plan.price_excl_vat * VAT_RATE).quantize(Decimal("0.01"))
        assert plan.price_incl_vat == plan.price_excl_vat + plan.vat
        assert plan.price_incl_vat.as_tuple().exponent == -2


def test_plans_are_listed_cheapest_first_and_give_more_for_more() -> None:
    prices = [plan.price_excl_vat for plan in PLANS]
    weekly = [plan.weekly_value_nok for plan in PLANS]
    monthly = [plan.monthly_value_nok for plan in PLANS]
    assert prices == sorted(prices)
    assert weekly == sorted(weekly)
    assert len(set(weekly)) == len(weekly)
    assert monthly == [amount * 4 for amount in weekly]
    assert len({plan.code for plan in PLANS}) == len(PLANS)


def test_an_unknown_code_is_no_plan() -> None:
    assert by_code("gratis") is None
    assert by_code("") is None


def test_llms_txt_quotes_the_cheapest_plan_at_its_price() -> None:
    cheapest = PLANS[0].price_excl_vat
    kroner = f"{cheapest:f}".removesuffix(".00").replace(".", ",")
    price_line = next(
        line
        for line in _LLMS_TXT.read_text(encoding="utf-8").splitlines()
        if line.startswith("- **Price.**")
    )
    assert f"from {kroner} kr a month excluding VAT" in price_line
