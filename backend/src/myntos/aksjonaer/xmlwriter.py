"""RF-1086 XML generation (Hovedskjema + Underskjema) and Skatteetaten prefill parsing.

Every element name, nesting order, ``gruppeid``, ``orid``, and number format
follows Skatteetaten's published XSDs verbatim:
- ``aksjonaerregisteroppgaveHovedskjema.xsd`` (``skjemanummer="890"``, ``RF-1086``)
- ``aksjonaerregisteroppgaveUnderskjema.xsd`` (``skjemanummer="923"``, ``RF-1086-U``)

Note that Skatteetaten's XSDs carry a few historic spellings
(``AksjerLividasjonMvAntall``, ``AksjerSlettedeLividasjonMvTidspunkt``,
``AksjonarUtenlandskIdenifikasjonsnummer``); those are preserved verbatim so
schema validation succeeds.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from myntos.aksjonaer.codes import (
    OVERDRAGELSE_SUBTYPES,
    SHARE_CLASS_CODES,
    EventLineRole,
    EventType,
    ShareholderKind,
)
from myntos.aksjonaer.compute import ComputedShareClass, ShareholderYearState
from myntos.domain.ledger.models import Company

_ZERO = Decimal("0")
_TWO = Decimal("0.01")
_SIX = Decimal("0.000001")
_POSTAL_4 = re.compile(r"^[0-9]{4}$")
_PHONE_CLEAN = re.compile(r"[^0-9+]")
_PHONE_VALID = re.compile(r"^[+]?[0-9]{1,14}$")


@dataclass(frozen=True)
class ContactInfo:
    email: str | None = None
    name: str | None = None
    role: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class BuiltFilingXml:
    """Complete canonical XML set for one share class and income year."""

    hovedskjema_xml: str
    underskjemaer: tuple[tuple[int, str, str], ...]  # (shareholder_id, name, xml)
    content_hash: str


def _fmt_decimal(value: Decimal, *, max_decimals: int = 2) -> str:
    """Format a Decimal matching XSD patterns without trailing noise.

    ``Decimal('100000.00')`` → ``'100000'``; ``Decimal('12.50')`` → ``'12.5'``;
    ``Decimal('-0.00')`` → ``'0'``.
    """
    quant = _TWO if max_decimals == 2 else _SIX
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    if rounded == _ZERO:
        return "0"
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _fmt_dt(day: date) -> str:
    return f"{day.isoformat()}T00:00:00"


def _add(parent: ET.Element, tag: str, *, orid: str, text: str) -> ET.Element:
    elem = ET.SubElement(parent, tag, {"orid": orid})
    elem.text = text
    return elem


def _grp(parent: ET.Element, tag: str, *, gruppeid: str) -> ET.Element:
    return ET.SubElement(parent, tag, {"gruppeid": gruppeid})


def _clean_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = _PHONE_CLEAN.sub("", raw.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if len(cleaned) > 15:
        cleaned = cleaned[:15]
    return cleaned if _PHONE_VALID.match(cleaned) else None


def _split_address(address: str | None) -> list[str]:
    """Split an address into at most two lines of 35 chars (XSD maxLength=35)."""
    if not address:
        return []
    raw_lines = [part.strip() for part in address.replace("\r", "").split("\n") if part.strip()]
    out: list[str] = []
    for line in raw_lines:
        if len(line) <= 35:
            out.append(line)
        else:
            out.append(line[:35].rstrip())
            rest = line[35:].strip()
            if rest:
                out.append(rest[:35].rstrip())
        if len(out) >= 2:
            break
    return out[:2]


def _serialise(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def build_hovedskjema(
    computed: ComputedShareClass,
    company: Company,
    contact: ContactInfo | None = None,
) -> str:
    """Build Skatteetaten RF-1086 Hovedskjema XML for one share class and year."""
    sc = computed.share_class
    root = ET.Element(
        "Skjema",
        {
            "skjemanummer": "890",
            "spesifikasjonsnummer": "12144",
            "blankettnummer": "RF-1086",
            "tittel": "Aksjonærregisteroppgaven",
            "gruppeid": "2586",
            "etatid": "974761076",
        },
    )

    gen = _grp(root, "GenerellInformasjon-grp-2587", gruppeid="2587")
    selskap = _grp(gen, "Selskap-grp-2588", gruppeid="2588")
    _add(selskap, "EnhetOrganisasjonsnummer-datadef-18", orid="18", text=company.orgnr)
    if company.name:
        _add(selskap, "EnhetNavn-datadef-1", orid="1", text=company.name.strip()[:175])
    if company.address_line:
        _add(selskap, "EnhetAdresse-datadef-15", orid="15", text=company.address_line.strip()[:105])
    postal = (company.postal_code or "").strip()
    if _POSTAL_4.match(postal):
        _add(selskap, "EnhetPostnummer-datadef-6673", orid="6673", text=postal)
    if company.city:
        _add(selskap, "EnhetPoststed-datadef-6674", orid="6674", text=company.city.strip()[:35])
    if sc.isin:
        _add(selskap, "EnhetISINNummer-datadef-17513", orid="17513", text=sc.isin[:35])
    _add(selskap, "AksjeType-datadef-17659", orid="17659", text=sc.code)
    _add(selskap, "Inntektsar-datadef-692", orid="692", text=str(computed.year))

    c_email = ((contact.email if contact else None) or company.email or "").strip()[:100]
    c_name = ((contact.name if contact else None) or "").strip()[:150]
    c_role = ((contact.role if contact else None) or "").strip()[:40]
    c_phone = _clean_phone((contact.phone if contact else None) or company.phone)
    if any((c_name, c_role, c_email, c_phone)):
        kp = _grp(gen, "Kontaktperson-grp-3442", gruppeid="3442")
        if c_name:
            _add(kp, "KontaktpersonSkjemaNavn-datadef-33918", orid="33918", text=c_name)
        if c_role:
            _add(kp, "KontaktpersonSkjemaRolle-datadef-33915", orid="33915", text=c_role)
        if c_email:
            _add(kp, "KontaktpersonSkjemaEPost-datadef-30533", orid="30533", text=c_email)
        if c_phone:
            _add(kp, "KontaktpersonSkjemaTelefonnummer-datadef-33916", orid="33916", text=c_phone)

    # Selskapsopplysninger (Post 1 - 6).
    oppl = _grp(root, "Selskapsopplysninger-grp-2589", gruppeid="2589")

    # Post 1: Aksjekapital for hele selskapet.
    ak_hele = _grp(oppl, "AksjekapitalForHeleSelskapet-grp-3443", gruppeid="3443")
    _add(
        ak_hele,
        "AksjekapitalFjoraret-datadef-7129",
        orid="7129",
        text=_fmt_decimal(computed.company_opening_capital),
    )
    _add(
        ak_hele,
        "Aksjekapital-datadef-87",
        orid="87",
        text=_fmt_decimal(computed.company_closing_capital),
    )

    # Post 2: Aksjekapital i denne aksjeklassen.
    ak_kl = _grp(oppl, "AksjekapitalIDenneAksjeklassen-grp-3444", gruppeid="3444")
    _add(
        ak_kl,
        "AksjekapitalISINAksjetypeFjoraret-datadef-17663",
        orid="17663",
        text=_fmt_decimal(computed.opening.share_capital),
    )
    _add(
        ak_kl,
        "AksjekapitalISINAksjetype-datadef-17664",
        orid="17664",
        text=_fmt_decimal(computed.closing.share_capital),
    )

    # Post 3: Pålydende per aksje.
    pal = _grp(oppl, "PalydendePerAksje-grp-3447", gruppeid="3447")
    _add(
        pal,
        "AksjeMvPalydendeFjoraret-datadef-23944",
        orid="23944",
        text=_fmt_decimal(computed.opening.par_value, max_decimals=6),
    )
    _add(
        pal,
        "AksjeMvPalydende-datadef-23945",
        orid="23945",
        text=_fmt_decimal(computed.closing.par_value, max_decimals=6),
    )

    # Post 4: Antall aksjer i denne aksjeklassen.
    ant = _grp(oppl, "AntallAksjerIDenneAksjeklassen-grp-3445", gruppeid="3445")
    _add(
        ant, "AksjerMvAntallFjoraret-datadef-29166", orid="29166", text=str(computed.opening.shares)
    )
    _add(ant, "AksjerMvAntall-datadef-29167", orid="29167", text=str(computed.closing.shares))

    # Post 5: Innbetalt aksjekapital i denne aksjeklassen.
    innb = _grp(oppl, "InnbetaltAksjekapitalIDenneAksjeklassen-grp-3446", gruppeid="3446")
    _add(
        innb,
        "AksjekapitalInnbetaltFjoraret-datadef-8020",
        orid="8020",
        text=_fmt_decimal(computed.opening.paid_in_capital),
    )
    _add(
        innb,
        "AksjekapitalInnbetalt-datadef-5867",
        orid="5867",
        text=_fmt_decimal(computed.closing.paid_in_capital),
    )

    # Post 6: Innbetalt overkurs i denne aksjeklassen (required = 0 on liquidation).
    ovk = _grp(oppl, "InnbetaltOverkursIDenneAksjeklassen-grp-3448", gruppeid="3448")
    _add(
        ovk,
        "AksjeOverkursISINAksjetypeFjoraret-datadef-17662",
        orid="17662",
        text=_fmt_decimal(computed.opening.paid_in_premium),
    )
    _add(
        ovk,
        "AksjeOverkursISINAksjetype-datadef-17661",
        orid="17661",
        text=_fmt_decimal(computed.closing.paid_in_premium),
    )

    # Partition corporate events into Skatteetaten's exact Hovedskjema sections.
    utbytte_evs = [
        item for item in computed.year_events if item[0].event_type == EventType.UTBYTTE.value
    ]
    post9_evs = [
        item
        for item in computed.year_events
        if item[0].event_type in (EventType.STIFTELSE.value, EventType.NYEMISJON.value)
    ]
    post10_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.SPLITT.value
        or (
            item[0].event_type == EventType.FONDSEMISJON.value
            and (item[0].subtype or "nye_aksjer") == "nye_aksjer"
        )
    ]
    post11_evs = [
        item for item in computed.year_events if item[0].event_type == EventType.LIKVIDASJON.value
    ]
    post12_evs = [
        item for item in computed.year_events if item[0].event_type == EventType.SPLEIS.value
    ]
    post13_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.KAPITALNEDSETTELSE.value
        and item[0].subtype == "overkurs"
    ]
    post14_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.FONDSEMISJON.value
        and item[0].subtype == "okning_palydende"
    ]
    post15_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.KAPITALFORHOYELSE_PALYDENDE.value
    ]
    post16_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.KAPITALNEDSETTELSE.value
        and item[0].subtype == "tapsdekning"
    ]
    post17_evs = [
        item
        for item in computed.year_events
        if item[0].event_type == EventType.KAPITALNEDSETTELSE.value
        and (item[0].subtype or "utbetaling") == "utbetaling"
    ]

    # Post 8: Utbytte.
    if utbytte_evs:
        utb_wrap = _grp(root, "Utbytte-grp-3449", gruppeid="3449")
        for ev, shares_at, _ in utbytte_evs:
            row = _grp(
                utb_wrap,
                "UtdeltSkatterettsligUtbytteILopetAvInntektsaret-grp-3451",
                gruppeid="3451",
            )
            total = ev.total_amount or _ZERO
            sub = ev.subtype or "Y"
            _add(
                row,
                "AksjeUtbytteISINAksjetype-datadef-17665",
                orid="17665",
                text=_fmt_decimal(total),
            )
            if sub == "Y":
                if ev.amount_per_share is not None:
                    per_share = ev.amount_per_share
                elif shares_at > 0:
                    per_share = (total / Decimal(shares_at)).quantize(_SIX, rounding=ROUND_HALF_UP)
                else:
                    per_share = _ZERO
                _add(
                    row,
                    "AksjeUtbyttePerAksje-datadef-23946",
                    orid="23946",
                    text=_fmt_decimal(per_share, max_decimals=6),
                )
            _add(row, "AksjeUtbytteHendelsestype-datadef-36564", orid="36564", text=sub)
            _add(
                row,
                "AksjeUtbytteTidspunkt-datadef-17667",
                orid="17667",
                text=_fmt_dt(ev.event_date),
            )

    # Post 9: Stiftelse / nyemisjon.
    if post9_evs:
        p9_wrap = _grp(root, "UtstedelseAvAksjerIfmStiftelseNyemisjonMv-grp-3452", gruppeid="3452")
        for ev, shares_after, par_after in post9_evs:
            row = _grp(p9_wrap, "AntallNyutstedteAksjer-grp-3453", gruppeid="3453")
            new_cnt = int(ev.total_shares or 0)
            sub = ev.subtype or "N"
            _add(
                row,
                "AksjerNyutstedteStiftelseMvAntall-datadef-17668",
                orid="17668",
                text=str(new_cnt),
            )
            _add(row, "AksjerStiftelseMvAntall-datadef-17669", orid="17669", text=str(shares_after))
            _add(row, "AksjerNyutstedteStiftelseMvType-datadef-17670", orid="17670", text=sub)
            _add(
                row,
                "AksjerNyutstedteStiftelseMvTidspunkt-datadef-17671",
                orid="17671",
                text=_fmt_dt(ev.event_date),
            )
            _add(
                row,
                "AksjerNyutstedteStiftelseMvPalydende-datadef-23947",
                orid="23947",
                text=_fmt_decimal(par_after, max_decimals=6),
            )
            if ev.premium_amount and ev.premium_amount > _ZERO and new_cnt > 0:
                ovk_per = (ev.premium_amount / Decimal(new_cnt)).quantize(
                    _SIX, rounding=ROUND_HALF_UP
                )
                _add(
                    row,
                    "AksjerNyutstedteStiftelseMvOverkurs-datadef-23948",
                    orid="23948",
                    text=_fmt_decimal(ovk_per, max_decimals=6),
                )

    # Post 10: Fondsemisjon (nye aksjer) / Splitt.
    if post10_evs:
        p10_wrap = _grp(root, "UtstedelseAvAksjerIfmFondsemisjonSplittMv-grp-3454", gruppeid="3454")
        for ev, shares_after, par_after in post10_evs:
            row = _grp(p10_wrap, "NyutstedteAksjerOmfordeling-grp-3455", gruppeid="3455")
            new_cnt = int(ev.total_shares or 0)
            code = "S" if ev.event_type == EventType.SPLITT.value else "E"
            _add(
                row,
                "AksjerNyutstedteFondsemisjonMvAntall-datadef-17677",
                orid="17677",
                text=str(new_cnt),
            )
            _add(
                row,
                "AksjerNyutstedteFondsemisjonMvAntallEtter-datadef-17678",
                orid="17678",
                text=str(shares_after),
            )
            _add(row, "AksjerNyutstedteFondsemisjonMvType-datadef-17679", orid="17679", text=code)
            _add(
                row,
                "AksjerNyutstedteFondsemisjonMvTidspunkt-datadef-17680",
                orid="17680",
                text=_fmt_dt(ev.event_date),
            )
            _add(
                row,
                "AksjerNyutstedteFondsemisjonMvPalydende-datadef-23949",
                orid="23949",
                text=_fmt_decimal(par_after, max_decimals=6),
            )

    # Post 11: Likvidasjon.
    if post11_evs:
        p11_wrap = _grp(
            root, "SlettingAvAksjerIfmLikvidasjonPartiellLikvidasjonMv-grp-3456", gruppeid="3456"
        )
        for ev, shares_after, _ in post11_evs:
            row = _grp(p11_wrap, "SlettedeAksjerAvgang-grp-3457", gruppeid="3457")
            deleted = int(ev.total_shares or computed.opening.shares or 1)
            par_deleted = ev.amount_per_share or computed.opening.par_value
            _add(
                row,
                "AksjerSlettedeLikvidasjonMvAntall-datadef-17688",
                orid="17688",
                text=str(deleted),
            )
            _add(
                row, "AksjerLividasjonMvAntall-datadef-17689", orid="17689", text=str(shares_after)
            )
            _add(
                row,
                "AksjerSlettedeLikvidasjonMvPalydende-datadef-23951",
                orid="23951",
                text=_fmt_decimal(par_deleted, max_decimals=6),
            )
            _add(row, "AksjerSlettedeLikvidasjonMvType-datadef-17691", orid="17691", text="L")
            _add(
                row,
                "AksjerSlettedeLividasjonMvTidspunkt-datadef-17692",
                orid="17692",
                text=_fmt_dt(ev.event_date),
            )
            if ev.total_amount is not None:
                _add(
                    row,
                    "AksjerSlettedeLikvidasjonMvVederlag-datadef-17770",
                    orid="17770",
                    text=_fmt_decimal(ev.total_amount),
                )

    # Post 12: Spleis.
    if post12_evs:
        p12_wrap = _grp(
            root, "SlettingAvAksjerIfmSpleisSkattefriFusjonFisjon-grp-3458", gruppeid="3458"
        )
        for ev, shares_after, par_after in post12_evs:
            row = _grp(p12_wrap, "SlettedeAksjerOmfordeling-grp-3459", gruppeid="3459")
            deleted = int(ev.total_shares or 1)
            _add(row, "AksjerSlettedeSpleisMvAntall-datadef-17693", orid="17693", text=str(deleted))
            _add(row, "AksjerSpleisAntall-datadef-17694", orid="17694", text=str(shares_after))
            _add(row, "AksjerSlettedeSpleisMvType-datadef-17695", orid="17695", text="V")
            _add(
                row,
                "AksjerSlettedeSpleisMvTidspunkt-datadef-17696",
                orid="17696",
                text=_fmt_dt(ev.event_date),
            )
            _add(
                row,
                "AksjerSlettedeSpleisPalydende-datadef-23953",
                orid="23953",
                text=_fmt_decimal(par_after, max_decimals=6),
            )

    # Post 13 - 17: Endringer i aksjekapital og overkurs.
    if any((post13_evs, post14_evs, post15_evs, post16_evs, post17_evs)):
        endr = _grp(root, "EndringerIAksjekapitalOgOverkurs-grp-3460", gruppeid="3460")
        for ev, _, _ in post13_evs:
            row = _grp(
                endr,
                "NedsettelseAvInnbetaltOverkursMedTilbakebetalingTilAksjonarene-grp-3461",
                gruppeid="3461",
            )
            _add(
                row,
                "AksjerOverkursNedsettelse-datadef-17707",
                orid="17707",
                text=_fmt_decimal(ev.total_amount or _ZERO),
            )
            _add(
                row,
                "AksjerOverkursNedsettelseTidspunkt-datadef-17708",
                orid="17708",
                text=_fmt_dt(ev.event_date),
            )
        for ev, shares_at, par_after in post14_evs:
            row = _grp(endr, "ForhoyelseAvAKVedOkningAvPalydende-grp-3462", gruppeid="3462")
            amt = ev.total_amount or _ZERO
            delta_par = (
                ev.amount_per_share
                if ev.amount_per_share is not None
                else (amt / Decimal(shares_at) if shares_at > 0 else _ZERO)
            )
            _add(
                row,
                "AksjekapitalForhoyelseFondsemisjon-datadef-17709",
                orid="17709",
                text=_fmt_decimal(amt),
            )
            _add(
                row,
                "AksjeFondsemisjonPalydendeForhoyelse-datadef-23956",
                orid="23956",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjePalydendeEtterFondsemisjon-datadef-23957",
                orid="23957",
                text=_fmt_decimal(par_after, max_decimals=6),
            )
            _add(
                row,
                "AksjeFondsemisjonTidspunkt-datadef-17712",
                orid="17712",
                text=_fmt_dt(ev.event_date),
            )
        for ev, shares_at, par_after in post15_evs:
            row = _grp(endr, "ForhoyelseAvAKVedOkningAvPalydende-grp-3463", gruppeid="3463")
            amt = ev.total_amount or _ZERO
            delta_par = (
                ev.amount_per_share
                if ev.amount_per_share is not None
                else (amt / Decimal(shares_at) if shares_at > 0 else _ZERO)
            )
            _add(
                row,
                "AksjekapitalNyemisjonForhoyelse-datadef-17713",
                orid="17713",
                text=_fmt_decimal(amt),
            )
            _add(
                row,
                "AksjeNyemisjonPalydendeForhoyelse-datadef-23958",
                orid="23958",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjePalydendeEtterNyemisjon-datadef-23959",
                orid="23959",
                text=_fmt_decimal(par_after, max_decimals=6),
            )
            _add(
                row,
                "AksjekapitalForhoyelsePalydendeHendelsestype-datadef-28268",
                orid="28268",
                text=ev.subtype or "6",
            )
            _add(
                row,
                "AksjeNyemisjonTidspunkt-datadef-17716",
                orid="17716",
                text=_fmt_dt(ev.event_date),
            )
            if ev.premium_amount and ev.premium_amount > _ZERO:
                _add(
                    row,
                    "AksjeOverkursForhoyelse-datadef-22071",
                    orid="22071",
                    text=_fmt_decimal(ev.premium_amount),
                )
        for ev, shares_at, par_after in post16_evs:
            row = _grp(endr, "NedsettelseAvInnbetaltOgFondsemittertAK-grp-3464", gruppeid="3464")
            amt = ev.total_amount or _ZERO
            delta_par = (
                ev.amount_per_share
                if ev.amount_per_share is not None
                else (amt / Decimal(shares_at) if shares_at > 0 else _ZERO)
            )
            _add(
                row,
                "AksjekapitalInnbetaltNedsettelse-datadef-17717",
                orid="17717",
                text=_fmt_decimal(amt),
            )
            _add(
                row,
                "AksjePalydendeNedsettelseTapsdekning-datadef-23960",
                orid="23960",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjePalydendeEtterNedsettelseTapsdekning-datadef-23961",
                orid="23961",
                text=_fmt_decimal(par_after, max_decimals=6),
            )
            _add(
                row,
                "AksjeNedsettelseTidspunkt-datadef-17720",
                orid="17720",
                text=_fmt_dt(ev.event_date),
            )
        for ev, shares_at, par_after in post17_evs:
            row = _grp(endr, "NedsettelseAKVedReduksjonAvPalydende-grp-3465", gruppeid="3465")
            amt = ev.total_amount or _ZERO
            delta_par = (
                ev.amount_per_share
                if ev.amount_per_share is not None
                else (amt / Decimal(shares_at) if shares_at > 0 else _ZERO)
            )
            _add(
                row,
                "AksjekapitalUtbetalingNedsettelse-datadef-17722",
                orid="17722",
                text=_fmt_decimal(amt),
            )
            _add(
                row,
                "AksjePalydendeNedsettelseUtbetaling-datadef-23962",
                orid="23962",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjePalydendeEtterNedsettelseUtbetaling-datadef-23963",
                orid="23963",
                text=_fmt_decimal(par_after, max_decimals=6),
            )
            _add(
                row,
                "AksjeNedsettelseTidspunkt-datadef-17725",
                orid="17725",
                text=_fmt_dt(ev.event_date),
            )

    return _serialise(root)


def build_underskjema(
    computed: ComputedShareClass,
    state: ShareholderYearState,
    company: Company,
) -> str:
    """Build Skatteetaten RF-1086-U Underskjema XML for one shareholder."""
    sc = computed.share_class
    holder = state.shareholder
    root = ET.Element(
        "Skjema",
        {
            "skjemanummer": "923",
            "spesifikasjonsnummer": "12232",
            "blankettnummer": "RF-1086-U",
            "tittel": "Aksjonærregisteroppgaven - underskjema",
            "gruppeid": "3983",
            "etatid": "974761076",
        },
    )

    top = _grp(root, "SelskapsOgAksjonaropplysninger-grp-3987", gruppeid="3987")
    sid = _grp(top, "Selskapsidentifikasjon-grp-3986", gruppeid="3986")
    _add(sid, "EnhetOrganisasjonsnummer-datadef-18", orid="18", text=company.orgnr)
    _add(sid, "AksjeType-datadef-17659", orid="17659", text=sc.code)
    if sc.isin:
        _add(sid, "EnhetISINNummer-datadef-17513", orid="17513", text=sc.isin[:35])
    _add(sid, "Inntektsar-datadef-692", orid="692", text=str(computed.year))

    aksj = _grp(top, "NorskUtenlandskAksjonar-grp-3988", gruppeid="3988")
    # Skatteetaten rule: emit at most ONE identifier (fnr OR orgnr OR UTL id).
    if holder.kind == ShareholderKind.PERSON.value and holder.national_id:
        _add(aksj, "AksjonarFodselsnummer-datadef-1156", orid="1156", text=holder.national_id)
    elif holder.kind == ShareholderKind.SELSKAP.value and holder.orgnr:
        _add(aksj, "AksjonarOrganisasjonsnummer-datadef-7597", orid="7597", text=holder.orgnr)
    elif holder.kind == ShareholderKind.UTENLANDSK.value and holder.foreign_id:
        _add(
            aksj,
            "AksjonarUtenlandskIdenifikasjonsnummer-datadef-26626",
            orid="26626",
            text=holder.foreign_id,
        )

    if holder.name:
        _add(aksj, "AksjonarNavn-datadef-1153", orid="1153", text=holder.name.strip()[:35])
    if holder.postal_code:
        _add(
            aksj,
            "AksjonarPostnummer-datadef-7598",
            orid="7598",
            text=holder.postal_code.strip()[:15],
        )
    if holder.city:
        _add(aksj, "AksjonarPoststed-datadef-7599", orid="7599", text=holder.city.strip()[:35])
    if holder.country_code:
        _add(
            aksj,
            "AksjonarLandkode-datadef-17740",
            orid="17740",
            text=holder.country_code.strip().upper(),
        )
    for addr_line in _split_address(holder.address):
        addr_grp = _grp(aksj, "Adresse-grp-7722", gruppeid="7722")
        _add(addr_grp, "AksjonarAdresse-datadef-1154", orid="1154", text=addr_line)

    # Antall aksjer og utbytte (Post 20, 21).
    ant_utb = _grp(
        root,
        "AntallAksjerUtbytteOgTilbakebetalingAvTidligereInnbetaltKapit-grp-3990",
        gruppeid="3990",
    )
    ant_grp = _grp(ant_utb, "AntallAksjerPerAksjonar-grp-3989", gruppeid="3989")
    _add(
        ant_grp, "AksjerAntallFjoraret-datadef-29168", orid="29168", text=str(state.opening_shares)
    )
    _add(
        ant_grp, "AksjonarAksjerAntall-datadef-17741", orid="17741", text=str(state.closing_shares)
    )

    div_entries = [se for se in state.events if se.event.event_type == EventType.UTBYTTE.value]
    wt_country: str | None = None
    wt_rate: int | None = None
    for se in div_entries:
        u_grp = _grp(ant_utb, "UtdeltUtbyttePerAksjonar-grp-3991", gruppeid="3991")
        _add(
            u_grp,
            "Aksjeutbytte-datadef-29169",
            orid="29169",
            text=_fmt_decimal(se.line.amount or _ZERO),
        )
        _add(u_grp, "AksjerUtbytteAntall-datadef-17742", orid="17742", text=str(se.line.shares))
        _add(
            u_grp,
            "AksjerUtbytteTidspunkt-datadef-17769",
            orid="17769",
            text=_fmt_dt(se.event.event_date),
        )
        if se.line.withholding_tax is not None:
            wt_whole = int(se.line.withholding_tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            _add(u_grp, "Kildeskatt-datadef-17743", orid="17743", text=str(wt_whole))
            wt_country = se.line.withholding_tax_country
            wt_rate = se.line.withholding_tax_rate
        _add(u_grp, "AutomatiskMotregningOnskerIkke-datadef-37159", orid="37159", text="0")

    if wt_country and wt_rate is not None:
        ks_grp = _grp(ant_utb, "UtdeltUtbytteKildeskatt-grp-9347", gruppeid="9347")
        _add(ks_grp, "KildeskattLandkode-datadef-17744", orid="17744", text=wt_country.upper())
        _add(ks_grp, "KildeskattProsent-datadef-2280", orid="2280", text=str(wt_rate))

    # Post 23: Tilgang (stiftelse, nyemisjon, kjøp/arv/gave).
    post23 = [
        se
        for se in state.events
        if se.event.event_type in (EventType.STIFTELSE.value, EventType.NYEMISJON.value)
        or (
            se.event.event_type == EventType.OVERDRAGELSE.value
            and se.line.role == EventLineRole.MOTTAKER.value
        )
    ]
    if post23:
        tr_wrap = _grp(root, "Transaksjoner-grp-3992", gruppeid="3992")
        kjop_wrap = _grp(tr_wrap, "KjopArvGaveStiftelseNyemisjonMv-grp-3993", gruppeid="3993")
        for se in post23:
            row = _grp(kjop_wrap, "AntallAksjerITilgang-grp-3998", gruppeid="3998")
            if se.event.event_type == EventType.OVERDRAGELSE.value:
                in_code, _, _ = OVERDRAGELSE_SUBTYPES.get(
                    se.event.subtype or "salg", ("K", "R", "")
                )
            else:
                in_code = se.event.subtype or "N"
            _add(row, "AksjerKjopAntall-datadef-12153", orid="12153", text=str(se.line.shares))
            _add(row, "AksjeErvervType-datadef-17745", orid="17745", text=in_code)
            _add(
                row,
                "AksjerErvervsdato-datadef-17746",
                orid="17746",
                text=_fmt_dt(se.event.event_date),
            )
            if se.line.amount is not None:
                _add(
                    row,
                    "AksjeAnskaffelsesverdi-datadef-17636",
                    orid="17636",
                    text=_fmt_decimal(se.line.amount),
                )
            if se.line.counterparty_national_id:
                _add(
                    row,
                    "AksjonarTidligereFodselsnummer-datadef-26530",
                    orid="26530",
                    text=se.line.counterparty_national_id,
                )
            elif se.line.counterparty_orgnr:
                _add(
                    row,
                    "AksjonarTidligereOrganisasjonsnummer-datadef-26531",
                    orid="26531",
                    text=se.line.counterparty_orgnr,
                )

    # Post 24: Tilgang ved fondsemisjon (nye aksjer) / splitt.
    post24 = [
        se
        for se in state.events
        if se.event.event_type == EventType.SPLITT.value
        or (
            se.event.event_type == EventType.FONDSEMISJON.value
            and (se.event.subtype or "nye_aksjer") == "nye_aksjer"
        )
    ]
    if post24:
        p24_wrap = _grp(
            root,
            "FondsemisjonSplittSkattefriFusjonFisjonSammenslaingDelingAv-grp-3994",
            gruppeid="3994",
        )
        for se in post24:
            row = _grp(p24_wrap, "AntallAksjerITilgangIfmOmfordeling-grp-3999", gruppeid="3999")
            code = "S" if se.event.event_type == EventType.SPLITT.value else "E"
            _add(
                row,
                "AksjerTilgangFondsemisjonMvAntall-datadef-17748",
                orid="17748",
                text=str(se.line.shares),
            )
            _add(row, "AksjerTilgangFondsemisjonMvType-datadef-17749", orid="17749", text=code)
            _add(
                row,
                "AksjerTilgangFondsemisjonMvTidspunkt-datadef-17750",
                orid="17750",
                text=_fmt_dt(se.event.event_date),
            )

    # Post 25: Avgang (salg, arv, gave, likvidasjon).
    post25 = [
        se
        for se in state.events
        if se.event.event_type == EventType.LIKVIDASJON.value
        or (
            se.event.event_type == EventType.OVERDRAGELSE.value
            and se.line.role == EventLineRole.AVGIVER.value
        )
    ]
    if post25:
        p25_wrap = _grp(
            root, "SalgArvGaveLikvidasjonPartiellLikvidasjonMv-grp-3995", gruppeid="3995"
        )
        for se in post25:
            row = _grp(p25_wrap, "AksjerIAvgang-grp-4002", gruppeid="4002")
            if se.event.event_type == EventType.LIKVIDASJON.value:
                out_code = "L"
            else:
                _, out_code, _ = OVERDRAGELSE_SUBTYPES.get(
                    se.event.subtype or "salg", ("K", "R", "")
                )
            _add(
                row, "AksjerArvMvOmsattAntall-datadef-17752", orid="17752", text=str(se.line.shares)
            )
            _add(row, "AksjerArvMvOmsattType-datadef-17753", orid="17753", text=out_code)
            _add(
                row,
                "AksjerArvMvOmsattTidspunkt-datadef-17754",
                orid="17754",
                text=_fmt_dt(se.event.event_date),
            )
            if se.line.amount is not None:
                _add(
                    row,
                    "AksjerArvMvOmsatt-datadef-17755",
                    orid="17755",
                    text=_fmt_decimal(se.line.amount),
                )
            if se.line.counterparty_national_id:
                _add(
                    row,
                    "AksjonarOvertakendeFodselsnummer-datadef-26532",
                    orid="26532",
                    text=se.line.counterparty_national_id,
                )
            elif se.line.counterparty_orgnr:
                _add(
                    row,
                    "AksjonarOvertakendeOrganisasjonsnummer-datadef-26533",
                    orid="26533",
                    text=se.line.counterparty_orgnr,
                )

    # Post 26: Avgang ved spleis.
    post26 = [se for se in state.events if se.event.event_type == EventType.SPLEIS.value]
    if post26:
        p26_wrap = _grp(root, "SpleisSkattefriFusjonOgSkattefriFisjon-grp-3996", gruppeid="3996")
        for se in post26:
            row = _grp(p26_wrap, "AntallAksjerIAvgangVedOmfordeling-grp-4003", gruppeid="4003")
            _add(
                row,
                "AksjerSpleisMvAvgangAntall-datadef-24007",
                orid="24007",
                text=str(se.line.shares),
            )
            _add(row, "AksjerSpleisMvType-datadef-17758", orid="17758", text="V")
            _add(
                row,
                "AksjerSpleisMvTidspunkt-datadef-17759",
                orid="17759",
                text=_fmt_dt(se.event.event_date),
            )

    # Post 27 - 29: Endringer i aksjekapital og overkurs per aksjonær.
    post27 = [
        se
        for se in state.events
        if se.event.event_type == EventType.KAPITALNEDSETTELSE.value
        and (se.event.subtype or "utbetaling") == "utbetaling"
    ]
    post28 = [
        se
        for se in state.events
        if se.event.event_type == EventType.KAPITALNEDSETTELSE.value
        and se.event.subtype == "overkurs"
    ]
    post29 = [
        se
        for se in state.events
        if se.event.event_type == EventType.KAPITALFORHOYELSE_PALYDENDE.value
    ]
    if any((post27, post28, post29)):
        endr = _grp(root, "EndringerIAksjekapitalOgOverkurs-grp-3997", gruppeid="3997")
        for se in post27:
            row = _grp(
                endr,
                "TilbakebetaltInnbetaltOgFondsemittertAKVedReduksjonAvPalydende-grp-4000",
                gruppeid="4000",
            )
            amt = se.line.amount or _ZERO
            delta_par = se.event.amount_per_share or (
                amt / Decimal(se.line.shares) if se.line.shares > 0 else _ZERO
            )
            _add(
                row,
                "AksjekapitalNedsettelseTilbakebetalt-datadef-17761",
                orid="17761",
                text=_fmt_decimal(amt),
            )
            _add(
                row,
                "AksjePalydendeRedusert-datadef-23970",
                orid="23970",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjeTilbakebetaltTidspunkt-datadef-17763",
                orid="17763",
                text=_fmt_dt(se.event.event_date),
            )
        for se in post28:
            row = _grp(
                endr, "TilbakebetaltTidligereInnbetaltOverkursForAksjen-grp-4001", gruppeid="4001"
            )
            _add(
                row,
                "OverkursTilbakebetalt-datadef-17765",
                orid="17765",
                text=_fmt_decimal(se.line.amount or _ZERO),
            )
            _add(
                row,
                "OverkursTilbakebetaltTidspunkt-datadef-17766",
                orid="17766",
                text=_fmt_dt(se.event.event_date),
            )
        for se in post29:
            row = _grp(endr, "ForhoyelseAvInnbetaltAksjekapitalVedOkning-grp-4987", gruppeid="4987")
            amt = se.line.amount or _ZERO
            delta_par = se.event.amount_per_share or (
                amt / Decimal(se.line.shares) if se.line.shares > 0 else _ZERO
            )
            _add(
                row,
                "AksjekapitalNyemisjonForhoyelseAksjonar-datadef-22073",
                orid="22073",
                text=_fmt_decimal(amt),
            )
            if se.line.paid_in_premium and se.line.paid_in_premium > _ZERO:
                _add(
                    row,
                    "AksjeOverkursForhoyelseAksjonar-datadef-22076",
                    orid="22076",
                    text=_fmt_decimal(se.line.paid_in_premium),
                )
            _add(
                row,
                "AksjeNyemisjonPalydendeForhoyelseAksjonar-datadef-23971",
                orid="23971",
                text=_fmt_decimal(delta_par, max_decimals=6),
            )
            _add(
                row,
                "AksjekapitalNyemisjonForhoyelsePalydendeTransaksjonstype-datadef-28267",
                orid="28267",
                text=se.event.subtype or "6",
            )
            _add(
                row,
                "AksjeNyemisjonTidspunktAksjonar-datadef-22075",
                orid="22075",
                text=_fmt_dt(se.event.event_date),
            )

    return _serialise(root)


def build_filing_xml(
    computed: ComputedShareClass,
    company: Company,
    contact: ContactInfo | None = None,
) -> BuiltFilingXml:
    """Build Hovedskjema + all Underskjemaer and hash them deterministically."""
    hoved = build_hovedskjema(computed, company, contact)
    unders: list[tuple[int, str, str]] = []
    hasher = hashlib.sha256()
    hasher.update(hoved.encode("utf-8"))
    for holder_state in computed.shareholders:
        xml = build_underskjema(computed, holder_state, company)
        unders.append((holder_state.shareholder.id, holder_state.shareholder.name, xml))
        hasher.update(b"\n---\n")
        hasher.update(xml.encode("utf-8"))
    return BuiltFilingXml(
        hovedskjema_xml=hoved,
        underskjemaer=tuple(unders),
        content_hash=hasher.hexdigest(),
    )


# --- Parsing Skatteetaten's GET /prefill/{year}/{orgnr} response ------------------


@dataclass(frozen=True)
class PrefillShareholder:
    share_class_code: str
    kind: str
    name: str
    national_id: str | None
    orgnr: str | None
    foreign_id: str | None
    address: str | None
    postal_code: str | None
    city: str | None
    country_code: str
    opening_shares: int


@dataclass(frozen=True)
class PrefillShareClass:
    code: str
    name: str
    isin: str | None
    opening_year: int
    opening_shares: int
    opening_par_value: Decimal
    opening_share_capital: Decimal
    opening_paid_in_capital: Decimal
    opening_paid_in_premium: Decimal


@dataclass(frozen=True)
class ParsedPrefill:
    share_classes: tuple[PrefillShareClass, ...]
    shareholders: tuple[PrefillShareholder, ...]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(root: ET.Element, target_local: str) -> str | None:
    for elem in root.iter():
        if _local(elem.tag) == target_local and elem.text is not None:
            text = elem.text.strip()
            if text:
                return text
    return None


def _find_decimal(root: ET.Element, target_local: str) -> Decimal:
    raw = _find_text(root, target_local)
    if raw is None:
        return _ZERO
    return Decimal(raw.replace(",", "."))


def _find_int(root: ET.Element, target_local: str) -> int:
    raw = _find_text(root, target_local)
    if raw is None:
        return 0
    return int(Decimal(raw.replace(",", ".")))


def _normalise_class_code(raw: str | None) -> str:
    if not raw:
        return "01"
    cleaned = raw.strip()
    if cleaned in SHARE_CLASS_CODES:
        return cleaned
    # Skatteetaten's prefill example occasionally writes "ordinaer" in XML.
    if cleaned.lower().startswith("ordin"):
        return "01"
    return "01"


def parse_prefill_documents(
    documents: Sequence[tuple[str | None, str]], *, for_year: int
) -> ParsedPrefill:
    """Parse ``(aksjeklasse, xml_string)`` pairs from Skatteetaten's prefill endpoint.

    Prefill for ``for_year`` carries the prior year's closing state in the
    ``Fjoraret`` fields (or closing fields when Skatteetaten returns last year's
    filed XML directly), which becomes the opening state for ``for_year - 1``
    (i.e. 01.01 of ``for_year``).
    """
    classes: dict[str, PrefillShareClass] = {}
    holders: list[PrefillShareholder] = []

    for hinted_class, xml_text in documents:
        try:
            root = ET.fromstring(xml_text)  # noqa: S314 - trusted Skatteetaten API response
        except ET.ParseError:
            continue
        blankett = (root.attrib.get("blankettnummer") or "").strip().upper()
        skjemanr = (root.attrib.get("skjemanummer") or "").strip()
        code = _normalise_class_code(hinted_class or _find_text(root, "AksjeType-datadef-17659"))

        if blankett == "RF-1086" or skjemanr == "890":
            # Prefill carries prior-year closing balances in either Fjoraret (when
            # Skatteetaten pre-rolled for for_year) or current fields (when returning
            # the prior year's document). Read Fjoraret when non-zero, else current.
            shares = _find_int(root, "AksjerMvAntallFjoraret-datadef-29166") or _find_int(
                root, "AksjerMvAntall-datadef-29167"
            )
            par = _find_decimal(root, "AksjeMvPalydendeFjoraret-datadef-23944") or _find_decimal(
                root, "AksjeMvPalydende-datadef-23945"
            )
            cap = _find_decimal(
                root, "AksjekapitalISINAksjetypeFjoraret-datadef-17663"
            ) or _find_decimal(root, "AksjekapitalISINAksjetype-datadef-17664")
            paid_in = _find_decimal(
                root, "AksjekapitalInnbetaltFjoraret-datadef-8020"
            ) or _find_decimal(root, "AksjekapitalInnbetalt-datadef-5867")
            premium = _find_decimal(
                root, "AksjeOverkursISINAksjetypeFjoraret-datadef-17662"
            ) or _find_decimal(root, "AksjeOverkursISINAksjetype-datadef-17661")
            isin = _find_text(root, "EnhetISINNummer-datadef-17513")
            classes[code] = PrefillShareClass(
                code=code,
                name=SHARE_CLASS_CODES.get(code, f"Aksjeklasse {code}"),
                isin=isin,
                opening_year=for_year - 1,
                opening_shares=shares,
                opening_par_value=par,
                opening_share_capital=cap,
                opening_paid_in_capital=paid_in,
                opening_paid_in_premium=premium,
            )
        elif blankett == "RF-1086-U" or skjemanr == "923":
            fnr = _find_text(root, "AksjonarFodselsnummer-datadef-1156")
            orgnr = _find_text(root, "AksjonarOrganisasjonsnummer-datadef-7597")
            utl = _find_text(root, "AksjonarUtenlandskIdenifikasjonsnummer-datadef-26626")
            name = _find_text(root, "AksjonarNavn-datadef-1153") or (
                f"Aksjonær {orgnr or utl or 'ukjent'}"
            )
            postal = _find_text(root, "AksjonarPostnummer-datadef-7598")
            city = _find_text(root, "AksjonarPoststed-datadef-7599")
            country = (_find_text(root, "AksjonarLandkode-datadef-17740") or "NO").upper()[:2]
            addr_parts = [
                elem.text.strip()
                for elem in root.iter()
                if _local(elem.tag) == "AksjonarAdresse-datadef-1154"
                and elem.text
                and elem.text.strip()
            ]
            if fnr:
                kind = ShareholderKind.PERSON.value
            elif orgnr:
                kind = ShareholderKind.SELSKAP.value
            else:
                kind = ShareholderKind.UTENLANDSK.value
            shares = _find_int(root, "AksjerAntallFjoraret-datadef-29168") or _find_int(
                root, "AksjonarAksjerAntall-datadef-17741"
            )
            holders.append(
                PrefillShareholder(
                    share_class_code=code,
                    kind=kind,
                    name=name,
                    national_id=fnr,
                    orgnr=orgnr,
                    foreign_id=utl,
                    address="\n".join(addr_parts) if addr_parts else None,
                    postal_code=postal,
                    city=city,
                    country_code=country,
                    opening_shares=shares,
                )
            )

    return ParsedPrefill(
        share_classes=tuple(classes.values()),
        shareholders=tuple(holders),
    )
