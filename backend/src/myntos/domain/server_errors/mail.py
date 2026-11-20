"""The one mail the error log causes: a line to the operator that something broke.

Plain text, to ``SERVER_ERROR_NOTIFY_TO`` and nobody else. It says what the
operator's page says about a kind of failure and nothing more: its number, the
logger, the log line's template, the route's template and the exception's
type. All of that is code, which is why the row may keep it (docs/operator.md);
mail leaves through Cloudflare without an EEA promise (docs/ARCHITECTURE.md,
"What the mail provider sees"), so code is also all it may carry. The values
are in the journal on the machine, where the page's ``journalctl`` line finds
them.

Built 2026-11-02, after an MVA-melding stopped at Altinn with a 500 and the
operator learnt it from the person who pressed the button: the page has to be
opened to be read, and nobody opens it on a hunch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from myntos.core.mail import Mail

# A deploy that breaks every route at once makes dozens of kinds. The mail is
# a ping, not the list; the page has the list.
MAX_NAMED = 5


@dataclass(frozen=True, slots=True)
class Failure:
    """What the mail may say about one kind of failure: the page's own columns."""

    number: int
    logger: str
    template: str
    route: str | None
    exception: str | None


def failure_notice(*, to: str, failures: Sequence[Failure], open_now: int, page: str) -> Mail:
    named = "\n".join(_line(failure) for failure in failures[:MAX_NAMED])
    rest = len(failures) - MAX_NAMED
    more = f"\n  … og {rest} til\n" if rest > 0 else ""
    first = (
        "en feil som ikke var åpen fra før"
        if len(failures) == 1
        else f"{len(failures)} feil som ikke var åpne fra før"
    )
    waiting = "Den er den eneste åpne nå." if open_now <= 1 else f"{open_now} er åpne nå."
    text = f"""Serveren i Myntos har logget {first}:

{named}
{more}
{waiting} Les dem på {page}

Verdiene står ikke her, bare koden som feilet. E-post går gjennom Cloudflare
uten noe løfte om EØS, og verdiene i en feil er ofte noens regnskap. Hver feil
på siden har kommandoen som henter resten fra journalen på maskinen.

Flere nye feil den neste timen får ingen egen e-post.

Hilsen Myntos
"""
    subject = (
        "Ny feil på serveren i Myntos"
        if len(failures) == 1
        else f"{len(failures)} nye feil på serveren i Myntos"
    )
    return Mail(to=to, subject=subject, text=text)


def _line(failure: Failure) -> str:
    where = f"{failure.route}, " if failure.route else ""
    kind = f"{failure.exception}, " if failure.exception else ""
    return f"  nr. {failure.number}: {where}{kind}{failure.logger}\n    «{failure.template}»"
