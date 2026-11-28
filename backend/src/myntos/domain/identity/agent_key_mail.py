"""What Myntos tells a person about the keys they made for their agents.

Three messages, for docs/agents-and-api.md rule 10: a key was made, a key was
taken into use, and a key was stopped because something looked wrong. Each goes
to the key's own person and nobody else, and each exists because a key is a way
into the books that works without anybody signing in. The first two are how a
person learns that somebody with their session made one, or that the key was
picked up by an agent that was not theirs; the third is how they learn that
their agent has stopped working, and why, before they find out by trying.

Two more for integration keys (rule 15), to the owner who made one: it was
made, and it stopped. A key that sends invoices on its own is worth a mail at
both ends, and a server whose key stopped notices only by failing, so the
owner hears it from Myntos first.

The same card as the account mails (:mod:`myntos.domain.identity.account_mail`),
and the same limits on what it may say (docs/ARCHITECTURE.md, "What the mail
provider sees"): the company's name and the key's name, never anything out of
the books.
"""

from __future__ import annotations

from myntos.core.mail import Mail
from myntos.domain.identity.account_mail import _greeting, _html, _mail
from myntos.domain.identity.models import AgentKeyStop


def _text(greeting: str, lead: str, url: str, notes: tuple[str, ...]) -> str:
    body = "\n\n".join(notes)
    return f"""{greeting}

{lead}

{url}

{body}

Hilsen Myntos
"""


def key_created_mail(
    *, to: str, full_name: str | None, key_name: str, company_name: str, url: str
) -> Mail:
    """Tell a person they made a key, so they know if they did not."""
    greeting = _greeting(full_name)
    lead = f"Du laget nøkkelen «{key_name}» for {company_name} i Myntos."
    notes = (
        "Nøkkelen lar en KI-agent eller et program lese i regnskapet, og gjøre det du ga den "
        "lov til, med din tilgang. Lim den bare inn hos en agent du stoler på.",
        "Var det ikke deg, har noen vært logget inn på kontoen din. Stopp nøkkelen under "
        "Innstillinger for selskapet → Tilgang med en gang, og bytt passord.",
    )
    return _mail(
        to=to,
        subject=f"Ny nøkkel for KI-agent i {company_name}",
        text=_text(greeting, lead, url, notes),
        html_body=_html(greeting=greeting, lead=lead, button="Se nøklene", url=url, notes=notes),
    )


def key_first_used_mail(
    *,
    to: str,
    full_name: str | None,
    key_name: str,
    company_name: str,
    client: str | None,
    url: str,
) -> Mail:
    """Tell a person their key was just picked up, and by what.

    Sent once per key, at the exchange, which is the only moment a key changes
    hands. After it the copy the person pasted opens nothing, and that is worth
    saying: it is the reason they do not have to worry about the chat.
    """
    greeting = _greeting(full_name)
    lead = f"En agent tok akkurat i bruk nøkkelen «{key_name}» for {company_name}."
    who = (
        f"Programmet som koblet til kalte seg «{client}». "
        if client
        else "Programmet som koblet til sa ikke hva det het. "
    )
    notes = (
        who + "Nøkkelen du kopierte virker ikke lenger, så kopien i samtalen med agenten kan "
        "ikke brukes av noen andre.",
        "Var det ikke din agent, stopp nøkkelen under Innstillinger for selskapet → Tilgang.",
    )
    return _mail(
        to=to,
        subject=f"Nøkkelen «{key_name}» er tatt i bruk",
        text=_text(greeting, lead, url, notes),
        html_body=_html(greeting=greeting, lead=lead, button="Se nøklene", url=url, notes=notes),
    )


def key_stopped_mail(
    *,
    to: str,
    full_name: str | None,
    key_name: str,
    company_name: str,
    reason: AgentKeyStop,
    url: str,
) -> Mail:
    """Tell a person we stopped their key, and why it was not their doing.

    Only for the two stops Myntos makes on its own judgement. Every other stop
    is something a person did, and they know.
    """
    greeting = _greeting(full_name)
    if reason is AgentKeyStop.REUSE:
        lead = (
            f"Vi stoppet nøkkelen «{key_name}» for {company_name}, fordi den ble brukt fra to "
            "steder."
        )
        why = (
            "En fornyelsesnøkkel som agenten allerede hadde brukt, kom tilbake. Da har trolig noen "
            "andre enn agenten din en kopi, så ingen kan bruke nøkkelen lenger."
        )
    else:
        lead = (
            f"Vi stoppet nøkkelen «{key_name}» for {company_name}, fordi den prøvde mye den ikke "
            "har lov til."
        )
        why = (
            "Nøkkelen ble avvist mange ganger på kort tid, for eksempel ved å spørre etter andre "
            "selskaper enn sitt eget. Slik oppfører en stjålet nøkkel seg, så vi stoppet den."
        )
    notes = (
        why,
        "Vil du fortsette med agenten, lag en ny nøkkel under Innstillinger for selskapet → "
        "Tilgang og lim den inn hos agenten. Regnskapet er ikke endret av at nøkkelen ble "
        "stoppet.",
    )
    return _mail(
        to=to,
        subject=f"Nøkkelen «{key_name}» er stoppet",
        text=_text(greeting, lead, url, notes),
        html_body=_html(greeting=greeting, lead=lead, button="Se nøklene", url=url, notes=notes),
    )


# --- Integration keys (docs/agents-and-api.md rule 15) -----------------------------


def integration_key_created_mail(
    *, to: str, full_name: str | None, key_name: str, company_name: str, url: str
) -> Mail:
    """Tell an owner they made an integration key, so they know if they did not.

    Never the secret: it was shown once on the page, and a mail is read by a
    provider that makes no promise about where it keeps it.
    """
    greeting = _greeting(full_name)
    lead = f"Du laget integrasjonsnøkkelen «{key_name}» for {company_name} i Myntos."
    notes = (
        "En integrasjonsnøkkel lar en server lage, utstede og sende fakturaer for selskapet uten "
        "å spørre deg, og bare fra adressene du skrev inn. Den legges i serverens innstillinger, "
        "aldri i en samtale med en KI.",
        "Var det ikke deg, har noen vært logget inn på kontoen din. Stopp nøkkelen under "
        "Innstillinger for selskapet → Integrasjoner med en gang, og bytt passord.",
    )
    return _mail(
        to=to,
        subject=f"Ny integrasjonsnøkkel i {company_name}",
        text=_text(greeting, lead, url, notes),
        html_body=_html(
            greeting=greeting, lead=lead, button="Se integrasjonene", url=url, notes=notes
        ),
    )


def integration_key_stopped_mail(
    *,
    to: str,
    full_name: str | None,
    key_name: str,
    company_name: str,
    reason: AgentKeyStop,
    url: str,
) -> Mail:
    """Tell an owner their integration key stopped, and why.

    Sent for every stop that reaches one key, the owner's own «Stopp»
    included: unlike an agent, the server that used it will not say so, it
    will only start failing.
    """
    greeting = _greeting(full_name)
    lead = f"Integrasjonsnøkkelen «{key_name}» for {company_name} virker ikke lenger."
    if reason is AgentKeyStop.REFUSALS:
        why = (
            "Myntos stoppet den fordi den ble avvist mange ganger på kort tid, for eksempel fra "
            "en adresse den ikke er laget for, eller for noe annet enn fakturering. Slik "
            "oppfører en stjålet nøkkel seg."
        )
    elif reason is AgentKeyStop.NOT_OWNER:
        why = "Du er ikke lenger eier av selskapet, og bare en eier kan ha en integrasjonsnøkkel."
    elif reason is AgentKeyStop.MEMBER_REMOVED:
        why = "Du har ikke lenger tilgang til selskapet."
    elif reason is AgentKeyStop.AGENT:
        why = "Serveren som hadde nøkkelen, ba selv om å stoppe den."
    else:
        why = "Nøkkelen ble stoppet i Myntos."
    notes = (
        why,
        "Serveren som brukte den, får nå avslag. Skal den fortsette, må en eier lage en ny "
        "nøkkel under Innstillinger for selskapet → Integrasjoner og legge den inn i "
        "serverens innstillinger. Regnskapet er ikke endret av at nøkkelen ble stoppet.",
    )
    return _mail(
        to=to,
        subject=f"Integrasjonsnøkkelen «{key_name}» er stoppet",
        text=_text(greeting, lead, url, notes),
        html_body=_html(
            greeting=greeting, lead=lead, button="Se integrasjonene", url=url, notes=notes
        ),
    )
