"""Deterministic analysis packet for «Dyp KI-analyse» (docs/analyse.md, WP6).

Everything a deep analysis model is allowed to know about a period and its
comparison is assembled here from :mod:`.figures` and :mod:`.tagging`, rounded
to whole kroner with every percentage and difference already computed. Two
reasons:

1. **The model never does arithmetic (rule 2).** Every figure, difference and
   share the report might want is already in the packet, and
   :mod:`.report` checks every number in the model's prose against this exact
   packet before anything is stored.
2. **Same packet, zero tokens (rule 6).** The canonical JSON is hashed together
   with the report's ``prompt_version`` and ``schema_version``. A second press
   over unchanged books at the same or a lower level returns the stored report
   without calling Løynd.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.analyse import figures, tagging, vocabulary
from myntos.domain.analyse.figures import Month, Part, Period, Side
from myntos.domain.ledger.models import Company

#: Top counterparties included on each side of the packet (docs/analyse.md).
TOP_COUNTERPARTIES = 10
#: Largest individual vouchers included with their summaries (rule 8 data block).
LARGEST_VOUCHERS = 15

#: Software-detected highlights included in the packet's ``oppdaget`` section.
NEW_COUNTERPARTY_MIN = Decimal("5000")
SPIKE_SHARE = Decimal("0.50")
SPIKE_KRONER = Decimal("5000")

#: Bumped when the packet structure itself changes meaningfully.
PACKET_SCHEMA_VERSION = 1

_EXCLUDED_SPIKE_SLUGS = frozenset({vocabulary.ANNET, figures.UNREAD_KEY})


def _kr(amount: Decimal) -> int:
    """Whole kroner, rounded half away from zero: the unit the report writes."""
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _pct(delta: Decimal, base: Decimal) -> int | None:
    """Whole percent change against ``base``, or None when ``base`` is not positive."""
    if base <= 0:
        return None
    return int(((delta / base) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _share_pct(part: Decimal, whole: Decimal) -> int | None:
    """Whole percent share of ``whole``, or None when ``whole`` is not positive."""
    if whole <= 0:
        return None
    return int(((part / whole) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _by_key(parts: list[Part]) -> dict[str, Part]:
    return {part.key: part for part in parts}


@dataclass(frozen=True)
class Packet:
    """The deterministic summary of one period, ready to hash and send."""

    period: Period
    has_comparison: bool
    ki_enabled: bool
    data: dict[str, Any]
    rendered: str
    input_hash: str


def compute_hash(rendered: str, *, prompt_version: int, schema_version: int) -> str:
    """SHA-256 over canonical packet JSON plus prompt and report schema versions."""
    material = f"p={prompt_version}|s={schema_version}|k={PACKET_SCHEMA_VERSION}\n{rendered}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _compare_parts(
    current: list[Part],
    previous: list[Part] | None,
    whole: Decimal,
    *,
    counts: dict[str, int] | None = None,
    limit: int | None = None,
    include_key: bool = False,
) -> list[dict[str, Any]]:
    prev_map = _by_key(previous) if previous is not None else None
    rows: list[dict[str, Any]] = []
    for part in current[:limit] if limit is not None else current:
        if part.amount == 0:
            continue
        entry: dict[str, Any] = {}
        if include_key:
            entry["merke"] = part.key
        entry["navn"] = part.label
        entry["belop"] = _kr(part.amount)
        if counts is not None:
            entry["antall_bilag"] = counts.get(part.key, 0)
        entry["andel_pst"] = _share_pct(part.amount, whole)
        if prev_map is not None:
            before_part = prev_map.get(part.key)
            before_amt = before_part.amount if before_part is not None else figures.ZERO
            diff = part.amount - before_amt
            entry["forrige"] = _kr(before_amt)
            entry["endring_kr"] = _kr(diff)
            entry["endring_pst"] = _pct(diff, before_amt)
        rows.append(entry)
    return rows


async def build(
    db: AsyncSession,
    *,
    company: Company,
    chosen: Period,
    prompt_version: int,
    schema_version: int,
    sammenlign: bool = True,
) -> Packet:
    """Build the deterministic packet for ``chosen`` and its comparison."""
    books = await figures.books(db, company=company)
    before = chosen.year_before() if sammenlign and books.cover(chosen.year_before()) else None

    totals = await figures.totals(db, company=company, chosen=chosen)
    forrige = (
        await figures.totals(db, company=company, chosen=before) if before is not None else None
    )

    months = chosen.months()
    monthly_now = await figures.per_month(db, company=company, chosen=chosen)
    monthly_prev: dict[Month, figures.Totals] | None = (
        await figures.per_month(db, company=company, chosen=before) if before is not None else None
    )
    prev_months = before.months() if before is not None else []

    booked_months = [m for m in months if books.has(m)]
    per_maned: list[dict[str, Any]] = []
    underskudd_maneder: list[dict[str, Any]] = []
    for idx, month in enumerate(months):
        label = figures.month_label(month)
        if not books.has(month):
            row: dict[str, Any] = {
                "maned": label,
                "inntekter": None,
                "kostnader": None,
                "resultat": None,
            }
        else:
            t = monthly_now.get(month, figures.Totals())
            row = {
                "maned": label,
                "inntekter": _kr(t.inntekter),
                "kostnader": _kr(t.kostnader),
                "resultat": _kr(t.resultat),
            }
            if t.resultat < 0:
                underskudd_maneder.append({"maned": label, "resultat": _kr(t.resultat)})
        if monthly_prev is not None and idx < len(prev_months):
            pm = prev_months[idx]
            if books.has(pm):
                pt = monthly_prev.get(pm, figures.Totals())
                row["forrige_inntekter"] = _kr(pt.inntekter)
                row["forrige_kostnader"] = _kr(pt.kostnader)
                row["forrige_resultat"] = _kr(pt.resultat)
        per_maned.append(row)

    # NS 4102 groups
    inc_groups = await figures.per_group(db, company=company, chosen=chosen, side=Side.INNTEKTER)
    cost_groups = await figures.per_group(db, company=company, chosen=chosen, side=Side.KOSTNADER)
    prev_inc_groups = (
        await figures.per_group(db, company=company, chosen=before, side=Side.INNTEKTER)
        if before is not None
        else None
    )
    prev_cost_groups = (
        await figures.per_group(db, company=company, chosen=before, side=Side.KOSTNADER)
        if before is not None
        else None
    )

    # Splits by hva (with voucher counts and untagged share)
    inc_reading = await figures.per_hva(db, company=company, chosen=chosen, side=Side.INNTEKTER)
    cost_reading = await figures.per_hva(db, company=company, chosen=chosen, side=Side.KOSTNADER)
    inc_counts = await figures.per_hva_counts(
        db, company=company, chosen=chosen, side=Side.INNTEKTER
    )
    cost_counts = await figures.per_hva_counts(
        db, company=company, chosen=chosen, side=Side.KOSTNADER
    )
    prev_inc_reading = (
        await figures.per_hva(db, company=company, chosen=before, side=Side.INNTEKTER)
        if before is not None and company.ki_enabled
        else None
    )
    prev_cost_reading = (
        await figures.per_hva(db, company=company, chosen=before, side=Side.KOSTNADER)
        if before is not None and company.ki_enabled
        else None
    )

    # Ten largest counterparties on each side (party, else motpart)
    inc_parties = await figures.per_counterparty(
        db, company=company, chosen=chosen, side=Side.INNTEKTER
    )
    cost_parties = await figures.per_counterparty(
        db, company=company, chosen=chosen, side=Side.KOSTNADER
    )
    prev_inc_parties = (
        await figures.per_counterparty(db, company=company, chosen=before, side=Side.INNTEKTER)
        if before is not None
        else None
    )
    prev_cost_parties = (
        await figures.per_counterparty(db, company=company, chosen=before, side=Side.KOSTNADER)
        if before is not None
        else None
    )

    # Recurring costs & fifteen largest vouchers
    recurring = await figures.recurring_costs(db, company=company, chosen=chosen)
    vouchers = await figures.largest_vouchers(
        db, company=company, chosen=chosen, limit=LARGEST_VOUCHERS
    )

    # Software-detected observations (oppdaget)
    nye_motparter: list[dict[str, Any]] = []
    if prev_cost_parties is not None:
        prev_cost_map = _by_key(prev_cost_parties)
        for part in cost_parties:
            if part.key == figures.NO_PARTY_KEY or part.amount <= NEW_COUNTERPARTY_MIN:
                continue
            prev_amt = prev_cost_map[part.key].amount if part.key in prev_cost_map else figures.ZERO
            if prev_amt == 0:
                nye_motparter.append({"navn": part.label, "belop": _kr(part.amount)})

    okte_hva: list[dict[str, Any]] = []
    if (
        prev_cost_reading is not None
        and cost_reading.dekning is not None
        and cost_reading.dekning >= figures.READ_ENOUGH
        and prev_cost_reading.dekning is not None
        and prev_cost_reading.dekning >= figures.READ_ENOUGH
    ):
        prev_hva_map = _by_key(prev_cost_reading.parts)
        for part in cost_reading.parts:
            if part.key in _EXCLUDED_SPIKE_SLUGS or part.amount <= 0:
                continue
            prev_part = prev_hva_map.get(part.key)
            if prev_part is None or prev_part.amount <= 0:
                continue
            diff = part.amount - prev_part.amount
            if diff > SPIKE_KRONER and diff / prev_part.amount > SPIKE_SHARE:
                okte_hva.append(
                    {
                        "navn": part.label,
                        "belop": _kr(part.amount),
                        "forrige": _kr(prev_part.amount),
                        "endring_kr": _kr(diff),
                        "endring_pst": _pct(diff, prev_part.amount),
                    }
                )

    nokkeltall: dict[str, Any] = {
        "inntekter": _kr(totals.inntekter),
        "kostnader": _kr(totals.kostnader),
        "resultat": _kr(totals.resultat),
    }
    if forrige is not None:
        inc_diff = totals.inntekter - forrige.inntekter
        cost_diff = totals.kostnader - forrige.kostnader
        res_diff = totals.resultat - forrige.resultat
        nokkeltall["forrige"] = {
            "inntekter": _kr(forrige.inntekter),
            "kostnader": _kr(forrige.kostnader),
            "resultat": _kr(forrige.resultat),
        }
        nokkeltall["endring"] = {
            "inntekter_kr": _kr(inc_diff),
            "inntekter_pst": _pct(inc_diff, forrige.inntekter),
            "kostnader_kr": _kr(cost_diff),
            "kostnader_pst": _pct(cost_diff, forrige.kostnader),
            "resultat_kr": _kr(res_diff),
        }

    def _hva_block(
        reading: figures.Reading,
        prev_reading: figures.Reading | None,
        counts: dict[str, int],
        whole: Decimal,
    ) -> dict[str, Any]:
        dekning_pst = (
            int((reading.dekning * 100).quantize(Decimal(1)))
            if reading.dekning is not None
            else None
        )
        ulest_pst = (100 - dekning_pst) if dekning_pst is not None else None
        compare_prev = (
            prev_reading.parts
            if prev_reading is not None
            and prev_reading.dekning is not None
            and prev_reading.dekning >= figures.READ_ENOUGH
            else None
        )
        return {
            "dekning_pst": dekning_pst,
            "ulest_andel_pst": ulest_pst,
            "ulest_belop": _kr(reading.unread),
            "deler": _compare_parts(
                reading.parts,
                compare_prev,
                whole,
                counts=counts,
                include_key=True,
            ),
        }

    data: dict[str, Any] = {
        "bedrift": tagging.company_line(company),
        "periode": {
            "fra": chosen.fra.isoformat(),
            "til": chosen.til.isoformat(),
            "maneder_i_perioden": len(months),
            "maneder_med_regnskap": len(booked_months),
        },
        "sammenligning": (
            {"fra": before.fra.isoformat(), "til": before.til.isoformat()}
            if before is not None
            else None
        ),
        "bokfort_til": books.last.isoformat() if books.last is not None else None,
        "nokkeltall": nokkeltall,
        "per_maned": per_maned,
        "inntekter_per_gruppe": _compare_parts(inc_groups, prev_inc_groups, totals.inntekter),
        "kostnader_per_gruppe": _compare_parts(cost_groups, prev_cost_groups, totals.kostnader),
        "inntekter_per_hva": _hva_block(
            inc_reading, prev_inc_reading, inc_counts, totals.inntekter
        ),
        "kostnader_per_hva": _hva_block(
            cost_reading, prev_cost_reading, cost_counts, totals.kostnader
        ),
        "storste_kunder": _compare_parts(
            [p for p in inc_parties if p.key != figures.NO_PARTY_KEY],
            prev_inc_parties,
            totals.inntekter,
            limit=TOP_COUNTERPARTIES,
        ),
        "storste_leverandorer": _compare_parts(
            [p for p in cost_parties if p.key != figures.NO_PARTY_KEY],
            prev_cost_parties,
            totals.kostnader,
            limit=TOP_COUNTERPARTIES,
        ),
        "faste_kostnader": [
            {
                "motpart": item.label,
                "maneder": item.months,
                "per_maned": _kr(item.average),
                "totalt": _kr(item.total),
                "trend_pst": item.trend_percent,
            }
            for item in recurring
        ],
        "storste_bilag": [
            {
                "bilag": v.bilag,
                "dato": v.dato.isoformat(),
                "retning": v.retning.value,
                "belop": _kr(v.belop),
                "konto": v.konto,
                "motpart": v.motpart,
                "oppsummering": v.oppsummering,
            }
            for v in vouchers
        ],
        "oppdaget": {
            "nye_motparter": nye_motparter,
            "okte_hva": okte_hva,
            "underskudd_maneder": underskudd_maneder,
        },
    }

    rendered = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    digest = compute_hash(rendered, prompt_version=prompt_version, schema_version=schema_version)
    return Packet(
        period=chosen,
        has_comparison=before is not None,
        ki_enabled=company.ki_enabled,
        data=data,
        rendered=rendered,
        input_hash=digest,
    )
