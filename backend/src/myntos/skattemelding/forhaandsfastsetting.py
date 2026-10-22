"""The melding a check starts from, prepared first for a wound-up company's last year.

In an ordinary year Skatteetaten serves the company's skattemelding from the
spring after, and the check fetches it. A company wound up during the year
files that year early, as a forhåndsfastsetting (skatteforvaltningsloven
§ 8-2), when Skatteetaten has no melding for it yet: ``GET`` answers 403
«Skatteplikt ikke registrert», or serves an ordinary one without the marker,
until it is asked to prepare one (``klargjoerforhaandsfastsetting``), and then
serves it a little later, marked with the format to file in. Proven in TT02
on 2026-10-22; docs/integrations/skatteetaten.md has what was seen.

Here, not in the route, so that the route and the TT02 diagnostic
(:mod:`myntos.auth.digdir_check`) run the same steps. No database: the caller
says whether the year is the company's last (``final``).
"""

from __future__ import annotations

import asyncio
from datetime import date

from myntos.integrations.skatteetaten import client as skattemelding_integration
from myntos.skattemelding import envelope
from myntos.skattemelding.errors import (
    SkattemeldingNotAvailableError,
    SkattemeldingYearNotSupportedError,
)

# How long a check waits for the melding Skatteetaten prepares: about 20
# seconds in TT02, and Skatteetaten's own notebook only says "litt". Past this
# the person is asked to press again, rather than the request held open.
WAITS: tuple[float, ...] = (5.0, 5.0, 5.0, 5.0, 5.0, 5.0)

# No melding for the taxpayer (yet): what a fetch answers before preparing.
_NOT_THERE = (403, 404)

# Skatteetaten's refusals of ``klargjoerforhaandsfastsetting``, in the person's
# terms (docs/api-v2, "Klargjør part for forhåndsfastsetting").
_REFUSALS = {
    "PART_HAR_UGYLDIG_STATUS_I_ER": (
        "Skatteetaten tar bare imot forhåndsfastsetting for et selskap Enhetsregisteret har "
        "registrert som oppløst, slettet, konkurs eller under tvangsoppløsning. Meld "
        "oppløsningen til Brønnøysundregistrene først, og kontroller igjen når den er registrert."
    ),
    "FORHAANDSFASTSETTING_ER_IKKE_STOETTET_FOR_INNTEKTSAAR": (
        "Skatteetaten tar ikke imot forhåndsfastsetting for {year}."
    ),
    "PART_TYPE_ER_IKKE_STOETTET": (
        "Skatteetaten tar bare imot forhåndsfastsetting for selskaper, ikke for personer."
    ),
}


async def _fetch(
    client: skattemelding_integration.SkattemeldingClient, orgnr: str, year: int, token: str
) -> envelope.FetchedMelding | None:
    """Gjeldende, or None when Skatteetaten has none for the taxpayer and year."""
    try:
        return envelope.parse_fetch(
            await client.fetch(inntektsaar=year, identifier=orgnr, id_porten_token=token)
        )
    except skattemelding_integration.SkatteetatenError as error:
        if error.status_code in _NOT_THERE:
            return None
        raise


async def fetch_for_check(
    client: skattemelding_integration.SkattemeldingClient,
    *,
    orgnr: str,
    year: int,
    token: str,
    final: bool,
    today: date,
) -> envelope.FetchedMelding:
    """The melding to complete and check: gjeldende, prepared first when it has to be.

    Asked to prepare only when needed: the fetch first, so a melding already
    prepared or filed is used as it is. For an ordinary year a missing melding
    is Skatteetaten's error as before (it raises). For the last year it is
    prepared while the calendar year runs; once the year is over, the ordinary
    melding is the one to file and is used as it comes.
    """
    if not final:
        return envelope.parse_fetch(
            await client.fetch(inntektsaar=year, identifier=orgnr, id_porten_token=token)
        )
    fetched = await _fetch(client, orgnr, year, token)
    if fetched is not None and (
        fetched.forhaandsfastsetting_format is not None or today > date(year, 12, 31)
    ):
        return fetched

    answer = await client.prepare_forhaandsfastsetting(
        inntektsaar=year, identifier=orgnr, id_porten_token=token
    )
    if answer not in (skattemelding_integration.PREPARED, "PART_HAR_GJELDENDE"):
        message = _REFUSALS.get(answer)
        raise SkattemeldingNotAvailableError(
            message.format(year=year)
            if message is not None
            else f"Skatteetaten kunne ikke gjøre klar skattemeldingen for {year} ({answer}). "
            "Prøv igjen senere."
        )
    for wait in WAITS:
        await asyncio.sleep(wait)
        fetched = await _fetch(client, orgnr, year, token)
        if fetched is None:
            continue
        # After PART_HAR_GJELDENDE there is nothing to wait for: what is there
        # is what Skatteetaten holds.
        if (
            fetched.forhaandsfastsetting_format is not None
            or answer != skattemelding_integration.PREPARED
        ):
            return fetched
    raise SkattemeldingNotAvailableError(
        f"Skatteetaten gjør klar skattemeldingen for {year} som forhåndsfastsetting. Det tar "
        "gjerne et minutt; trykk «Kontroller hos Skatteetaten» igjen om litt."
    )


def is_early(fetched: envelope.FetchedMelding, *, final: bool, year: int) -> bool:
    """Whether the melding is a forhåndsfastsetting, refused where Myntos cannot file it so."""
    found = fetched.forhaandsfastsetting_format
    if found is None:
        return False
    if not final:
        raise SkattemeldingNotAvailableError(
            f"Skatteetaten har gjort klar skattemeldingen for {year} som forhåndsfastsetting, "
            "for et selskap som er oppløst, men regnskapet i Myntos løper til 31. desember. "
            "Avslutt regnskapet ved oppløsningen under Årsavslutning, og kontroller igjen."
        )
    if found != envelope.THIS_YEARS_FORMAT:
        raise SkattemeldingYearNotSupportedError(
            f"Skatteetaten vil ha forhåndsfastsettingen for {year} i fjorårets skjema, fordi "
            f"skjemaet for {year} ikke er klart ennå. Det lager ikke Myntos; lever den på "
            "skatteetaten.no, eller kontroller igjen når Skatteetaten har publisert årets skjema."
        )
    return True
