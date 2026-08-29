"""Valutakurser fra Norges Bank.

A bilag in GBP states one number the ledger cannot use. Hovedboken is kept in
kroner, so a total of 2 137,00 GBP has to become a total in kroner before it can
be posted, and the multiplier is a fact about a particular day rather than
anything the document says.

That fact is published. Norges Bank puts its daily rates in an open SDMX API
with no key and no terms to sign, and it is the rate a Norwegian accountant is
expected to reach for: bokføringsforskriften § 1-3 lets a transaction in foreign
currency be converted at the rate on the transaction date, and Norges Bank's
midkurs is the one everyone means by "kursen den dagen".

**This is a lookup, not a guess, and that is the point.** The category on a
bilag is a judgement and goes to a model; the exchange rate is a published
number and goes here. Nothing in this module asks anyone's opinion, so the
answer is the same every time it is asked and can be shown to the user with the
date it was quoted on next to it.

**It is still only a proposal.** What the account was actually charged includes
the card issuer's spread and any fee, and that number lives on the bank
statement rather than on the invoice. So the converted amount is offered as a
starting point with its own :class:`~myntos.domain.bilag.interpretation.FieldSource`,
and the user is told to check it. Getting the user from an empty field to a
number that is within a percent of right is worth a great deal; pretending it is
the final number is not.

Nothing here raises for the user. A rate that cannot be fetched leaves the
amount field exactly as empty as it is today, which is the behaviour this module
improves on rather than replaces.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://data.norges-bank.no/api/data/EXR"

# Short, because a user is watching a spinner and an empty amount field is a
# survivable outcome. The conversion is a convenience laid on top of a reading
# that is already complete without it.
_HTTP_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

# Rates are quoted on business days. A bilag dated on a Saturday, or during
# påske, has no rate of its own, and the convention is to use the last one
# quoted before it. Ten days covers every Norwegian holiday stretch; beyond that
# the date is wrong rather than the market being closed.
LOOKBACK_DAYS = 10

# Norges Bank quotes some currencies per hundred units (JPY, ISK, HUF …) and
# says so in UNIT_MULT. Dividing by it is not a detail: reading 5,8654 as the
# price of one yen rather than of a hundred is a hundredfold error in a field
# the user is being invited to trust.
_UNIT_MULT = "UNIT_MULT"
_TIME_PERIOD = "TIME_PERIOD"
_OBS_VALUE = "OBS_VALUE"


class RateUnavailableError(Exception):
    """No rate could be established. The caller leaves the amount to the user."""


@dataclass(frozen=True)
class Rate:
    """One published rate, and the day it was published for."""

    currency_code: str
    # The day the rate is quoted for, which is on or before the day asked about.
    quoted_on: date
    # Kroner for one unit of the currency, with the unit multiplier already
    # divided out, so a caller can multiply and be done.
    kroner_per_unit: Decimal

    def to_kroner(self, amount: Decimal) -> Decimal:
        """``amount`` in this currency, as kroner rounded to the øre."""
        return (amount * self.kroner_per_unit).quantize(Decimal("0.01"))


# One process, one cache. A daily rate does not change once published, and the
# same bilag re-uploaded twice should not become two requests to Norges Bank.
# Bounded because the keys are attacker-influenced only in the sense that a user
# may upload many documents; a few thousand entries is nothing and the process
# restarts often enough that staleness is not a concern for a fixed number.
_CACHE: dict[tuple[str, date], Rate] = {}
_CACHE_LIMIT = 4096


def _parse(body: str, *, currency_code: str) -> Rate | None:
    """The last quoted row in a Norges Bank CSV response.

    The API answers in ascending date order and the request already bounds the
    end at the day we asked about, so the final row is the most recent rate on
    or before it. Rows are read by column name rather than by position: the
    response carries both a code and a label for every dimension, and counting
    columns is how a future added dimension turns into a silently wrong number.
    """
    rows = list(csv.DictReader(io.StringIO(body), delimiter=";"))
    for row in reversed(rows):
        raw_value = (row.get(_OBS_VALUE) or "").strip()
        raw_day = (row.get(_TIME_PERIOD) or "").strip()
        if not raw_value or not raw_day:
            continue
        try:
            value = Decimal(raw_value)
            quoted_on = date.fromisoformat(raw_day)
            multiplier = int((row.get(_UNIT_MULT) or "0").strip() or "0")
        except (InvalidOperation, ValueError):
            continue
        if value <= 0 or not 0 <= multiplier <= 6:
            continue
        return Rate(
            currency_code=currency_code,
            quoted_on=quoted_on,
            kroner_per_unit=value / (Decimal(10) ** multiplier),
        )
    return None


async def rate_for(currency_code: str, *, on: date) -> Rate:
    """Kroner per unit of ``currency_code``, as quoted on or before ``on``.

    Raises :class:`RateUnavailableError` for everything: an unreachable API, a
    currency Norges Bank does not quote, and a window with no observation in it
    all mean the same thing to the caller, which is that the amount field stays
    the user's to fill in.
    """
    code = currency_code.upper()
    if code == "NOK":
        return Rate(currency_code="NOK", quoted_on=on, kroner_per_unit=Decimal(1))
    if len(code) != 3 or not code.isalpha():
        raise RateUnavailableError(f"{currency_code} er ikke en valutakode")

    cached = _CACHE.get((code, on))
    if cached is not None:
        return cached

    params = {
        "format": "csv",
        "startPeriod": (on - timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "endPeriod": on.isoformat(),
        "locale": "en",
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}/B.{code}.NOK.SP", params=params)
            # 404 is Norges Bank's answer for a currency it does not quote, and
            # is no more of an error here than an empty window is.
            response.raise_for_status()
            body = response.text
    except httpx.HTTPError as error:
        logger.info("Norges Bank did not answer for %s: %s", code, type(error).__name__)
        raise RateUnavailableError(f"fikk ikke kurs for {code}") from error

    rate = _parse(body, currency_code=code)
    if rate is None:
        raise RateUnavailableError(f"Norges Bank kvoterer ingen kurs for {code} rundt {on}")
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[(code, on)] = rate
    return rate
