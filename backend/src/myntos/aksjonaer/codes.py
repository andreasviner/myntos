"""Official Skatteetaten RF-1086 code tables and identifier validators.

Every code value below matches Skatteetaten's published XSDs
(``aksjonaerregisteroppgaveHovedskjema.xsd`` and
``aksjonaerregisteroppgaveUnderskjema.xsd``). Keeping the closed sets and their
Norwegian labels in one module guarantees the API, pre-flight checks, XML
builder, and frontend always agree.
"""

from __future__ import annotations

import enum
import re

from myntos.aksjonaer.errors import InvalidAksjonaerDataError
from myntos.domain.ledger.errors import InvalidOrgnrError
from myntos.domain.ledger.orgnr import validate_orgnr

# Skatteetaten's KodelisteAksjeklasse (01..13).
SHARE_CLASS_CODES: dict[str, str] = {
    "01": "Ordinære aksjer",
    "02": "A-aksjer",
    "03": "B-aksjer",
    "04": "C-aksjer",
    "05": "Preferanseaksjer",
    "06": "Andre aksjer",
    "07": "D-aksjer",
    "08": "E-aksjer",
    "09": "F-aksjer",
    "10": "G-aksjer",
    "11": "H-aksjer",
    "12": "I-aksjer",
    "13": "J-aksjer",
}


class ShareholderKind(enum.StrEnum):
    PERSON = "person"
    SELSKAP = "selskap"
    UTENLANDSK = "utenlandsk"


class EventType(enum.StrEnum):
    STIFTELSE = "stiftelse"
    NYEMISJON = "nyemisjon"
    OVERDRAGELSE = "overdragelse"
    UTBYTTE = "utbytte"
    SPLITT = "splitt"
    SPLEIS = "spleis"
    FONDSEMISJON = "fondsemisjon"
    KAPITALFORHOYELSE_PALYDENDE = "kapitalforhoyelse_palydende"
    KAPITALNEDSETTELSE = "kapitalnedsettelse"
    LIKVIDASJON = "likvidasjon"


class EventLineRole(enum.StrEnum):
    MOTTAKER = "mottaker"
    AVGIVER = "avgiver"


# Post 8 (Hovedskjema) dividend event types.
UTBYTTE_SUBTYPES: dict[str, str] = {
    "Y": "Ordinært eller tilleggsutbytte",
    "Z": "Skattepliktig lån eller annen utdeling",
}

# Post 9 (Hovedskjema) and Post 23 (Underskjema) issuance types.
EMISJON_SUBTYPES: dict[str, str] = {
    "N": "Stiftelse eller nyemisjon (kontantinnskudd)",
    "NF": "Stiftelse eller nyemisjon med tingsinnskudd",
    "NK": "Nyemisjon ved konvertering av fordring",
    "NAK": "Nyemisjon rettet mot ansatte",
    "TN": "Stiftelse eller nyemisjon med skatteinsentiv for oppstartsselskap",
}

# Transfer subtypes: maps user/domain code to (buyer Post 23 code, seller Post 25 code, label).
OVERDRAGELSE_SUBTYPES: dict[str, tuple[str, str, str]] = {
    "salg": ("K", "R", "Kjøp og salg"),
    "arv": ("A", "A", "Arv (uten kontinuitet)"),
    "gave": ("G", "G", "Gave (uten kontinuitet)"),
    "arv_gave_kontinuitet": ("GK", "GK", "Arv eller gave med kontinuitet"),
    "overforing_kontinuitet": ("OK", "OK", "Overføring med skattemessig kontinuitet"),
    "skilsmisse": ("ES", "ES", "Fordeling mellom ektefeller ved skilsmisse"),
}

# Transfer codes whose Skatteetaten business validation requires counterparty ID
# on Post 23 / Post 25 (docs/api/innrapportering-aksjonaerregisteroppgave.md).
CONTINUITY_FNR_REQUIRED: frozenset[str] = frozenset({"AK", "GS", "ES"})
CONTINUITY_ANY_ID_REQUIRED: frozenset[str] = frozenset({"OK", "GK"})

# Fondsemisjon subtypes: new shares (Post 10/24) vs increased par value (Post 14).
FONDSEMISJON_SUBTYPES: dict[str, str] = {
    "nye_aksjer": "Fondsemisjon med nye aksjer",
    "okning_palydende": "Fondsemisjon ved økning av pålydende",
}

# Kapitalnedsettelse subtypes (Post 17+27, Post 16, Post 13+28).
KAPITALNEDSETTELSE_SUBTYPES: dict[str, str] = {
    "utbetaling": "Nedsettelse av pålydende med utbetaling til aksjonærene",
    "tapsdekning": "Nedsettelse av pålydende til dekning av tap",
    "overkurs": "Tilbakebetaling av tidligere innbetalt overkurs",
}

_FOREIGN_ID_RE = re.compile(r"^UTL[0-9]{9}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_ISIN_RE = re.compile(r"^[A-Z0-9]{1,35}$")
_FNR_W1 = (3, 7, 6, 1, 8, 9, 4, 5, 2)
_FNR_W2 = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def validate_share_class_code(code: str) -> str:
    cleaned = code.strip()
    if cleaned not in SHARE_CLASS_CODES:
        raise InvalidAksjonaerDataError(
            "aksjeklassekode må være en gyldig kode fra Skatteetatens liste (01 til 13)"
        )
    return cleaned


def validate_country_code(code: str) -> str:
    cleaned = code.strip().upper()
    if not _COUNTRY_RE.match(cleaned):
        raise InvalidAksjonaerDataError("landkode må være to store bokstaver (for eksempel NO)")
    return cleaned


def validate_isin(isin: str | None) -> str | None:
    if isin is None:
        return None
    cleaned = isin.strip().upper()
    if not cleaned:
        return None
    if not _ISIN_RE.match(cleaned):
        raise InvalidAksjonaerDataError(
            "ISIN kan bare inneholde bokstaver og siffer (maks 35 tegn)"
        )
    return cleaned


def validate_national_id(raw: str) -> str:
    """Normalise and mod-11 check a Norwegian fødselsnummer or D-nummer.

    Checks both control digits (k1 and k2). Accepts real fødselsnumre,
    D-numre (day +40), and Skatteetaten Tenor synthetic numbers (month +80),
    all of which satisfy the standard Norwegian mod-11 weights.
    """
    digits = raw.replace(" ", "").replace("-", "")
    if len(digits) != 11 or not digits.isascii() or not digits.isdigit():
        raise InvalidAksjonaerDataError("fødselsnummer eller D-nummer må være 11 siffer")
    nums = [int(ch) for ch in digits]
    r1 = sum(a * b for a, b in zip(nums[:9], _FNR_W1, strict=True)) % 11
    k1 = 0 if r1 == 0 else 11 - r1
    if k1 == 10 or k1 != nums[9]:
        raise InvalidAksjonaerDataError("fødselsnummeret har feil kontrollsiffer")
    r2 = sum(a * b for a, b in zip(nums[:10], _FNR_W2, strict=True)) % 11
    k2 = 0 if r2 == 0 else 11 - r2
    if k2 == 10 or k2 != nums[10]:
        raise InvalidAksjonaerDataError("fødselsnummeret har feil kontrollsiffer")
    return digits


def mask_national_id(national_id: str | None) -> str | None:
    """Return ``DDMMYY *****`` so lists never expose the personal suffix."""
    if not national_id or len(national_id) != 11:
        return None
    return f"{national_id[:6]} *****"


def validate_shareholder_orgnr(raw: str) -> str:
    try:
        return validate_orgnr(raw)
    except InvalidOrgnrError as err:
        raise InvalidAksjonaerDataError(str(err)) from err


def validate_foreign_id(raw: str) -> str:
    cleaned = raw.strip().upper().replace(" ", "")
    if not _FOREIGN_ID_RE.match(cleaned):
        raise InvalidAksjonaerDataError(
            "utenlandsk aksjonær-ID må ha formen UTL etterfulgt av 9 siffer"
        )
    return cleaned
