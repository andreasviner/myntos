"""The messages Myntos sends a person about their own account.

Confirming an address and resetting a password, and three that exist because a
BankID account signs in with BankID and nothing else (docs/integrations/
bankid.md): the answer to a reset request for one, which carries no link that
sets anything; the link that moves one to e-mail and password; and the notice
that an account has just been switched to BankID. Kept apart from the invitation
(:mod:`myntos.domain.identity.invitation_mail`) because the reader is in a
different position: they asked for this message a moment ago, so it has no
stranger to introduce and no case to make. Each says what the link does, how
long it works, and what to do if they did not ask, and nothing more.

Plain text is the message. The HTML part is the same words with one button,
because a person on a phone looks for the thing to press; it carries the mark
inline, for the reasons :mod:`myntos.core.brand` gives, and nothing that is
fetched when the message is opened. No tracking pixel, no redirecting link
wrapper and no query parameters beyond the token: the only party that learns
the message was read is the person reading it, and only by clicking.

The "if you did not ask" line is not boilerplate. A reset message nobody asked
for is how a person learns somebody is trying their address, and the sentence
has to tell them that doing nothing is safe, because it is: the link dies on its
own and the password is unchanged until somebody follows it.
"""

from __future__ import annotations

import html
from datetime import datetime

from myntos.core.brand import INK, LOGO_CID, LOGO_WIDTH, logo_png
from myntos.core.mail import InlineImage, Mail
from myntos.domain.identity.invitation_mail import _first_name, _norwegian_date

_QUIET = "#5f6661"
_FAINT = "#8a8f8b"
_RULE = "#e8e8e3"


def _greeting(full_name: str | None) -> str:
    first = _first_name(full_name)
    return f"Hei {first}," if first is not None else "Hei,"


def _html(*, greeting: str, lead: str, button: str, url: str, notes: tuple[str, ...]) -> str:
    """The shared card: mark, greeting, one sentence, one button, notes, raw link.

    Every argument is escaped here, so a caller cannot forget to: the greeting
    carries a name somebody typed.
    """
    safe_url = html.escape(url, quote=True)
    notes_markup = "".join(
        f'<p style="margin:0 0 10px;font-size:0.85rem;color:{_QUIET};">{html.escape(note)}</p>'
        for note in notes
    )
    return f"""<!doctype html>
<html lang="nb">
  <head>
    <meta charset="utf-8" />
    <meta name="color-scheme" content="light" />
    <meta name="supported-color-schemes" content="light" />
  </head>
  <body style="margin:0;padding:24px;background:#f6f6f4;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
               color:{INK};line-height:1.55;">
    <div style="max-width:34rem;margin:0 auto;background:#fff;border:1px solid #e4e4df;
                border-radius:12px;padding:28px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             style="margin:0 0 24px;border-collapse:collapse;">
        <tr>
          <td style="padding:0 10px 0 0;vertical-align:middle;">
            <img src="cid:{LOGO_CID}" alt="Myntos" width="{LOGO_WIDTH}" height="{LOGO_WIDTH}"
                 style="display:block;width:{LOGO_WIDTH}px;height:{LOGO_WIDTH}px;border:0;" />
          </td>
          <td style="vertical-align:middle;font-size:1.35rem;font-weight:700;
                     letter-spacing:-0.01em;color:{INK};">Myntos</td>
        </tr>
      </table>
      <p style="margin:0 0 8px;font-size:0.95rem;color:{_QUIET};">{html.escape(greeting)}</p>
      <p style="margin:0 0 22px;font-size:1.05rem;font-weight:600;line-height:1.4;
                color:{INK};">{html.escape(lead)}</p>
      <p style="margin:0 0 22px;">
        <a href="{safe_url}"
           style="display:inline-block;background:#1b6b4a;color:#fff;text-decoration:none;
                  padding:13px 22px;border-radius:8px;font-weight:600;font-size:1rem;"
           >{html.escape(button)}</a>
      </p>
      {notes_markup}
      <p style="margin:12px 0 0;padding-top:15px;border-top:1px solid {_RULE};
                font-size:0.78rem;color:{_FAINT};">
        Virker ikke knappen, kopier denne lenken inn i nettleseren:<br />
        <a href="{safe_url}" style="color:{_FAINT};word-break:break-all;">{safe_url}</a>
      </p>
    </div>
  </body>
</html>
"""


def _mail(*, to: str, subject: str, text: str, html_body: str) -> Mail:
    # No reply_to: the deployment's own MAIL_REPLY_TO applies, if any. There is
    # no colleague behind these messages for a reply to reach.
    return Mail(
        to=to,
        subject=subject,
        text=text,
        html=html_body,
        images=(InlineImage(cid=LOGO_CID, data=logo_png()),),
    )


def verification_mail(*, to: str, full_name: str | None, url: str, expires_at: datetime) -> Mail:
    """Ask the owner of ``to`` to confirm that it is theirs."""
    greeting = _greeting(full_name)
    deadline = _norwegian_date(expires_at)
    lead = "Bekreft at denne e-postadressen er din, så vet vi hvor vi kan nå deg."
    notes = (
        f"Lenken virker til {deadline}, og bare én gang.",
        "Har du ikke laget en Myntos-konto med denne adressen, kan du se bort fra "
        "denne e-posten. Ingenting skjer før noen følger lenken.",
    )
    text = f"""{greeting}

{lead}

{url}

{notes[0]}

{notes[1]}

Hilsen Myntos
"""
    return _mail(
        to=to,
        subject="Bekreft e-postadressen din for Myntos",
        text=text,
        html_body=_html(
            greeting=greeting,
            lead=lead,
            button="Bekreft e-postadressen",
            url=url,
            notes=notes,
        ),
    )


def password_reset_mail(*, to: str, full_name: str | None, url: str, minutes: int) -> Mail:
    """Send the owner of ``to`` a link that sets a new password."""
    greeting = _greeting(full_name)
    lead = "Noen ba om et nytt passord til Myntos-kontoen din. Var det deg, velger du det her."
    notes = (
        f"Lenken virker i {minutes} minutter, og bare én gang. Når du har valgt et nytt "
        "passord, blir alle som er logget inn på kontoen logget ut.",
        "Ba du ikke om dette, trenger du ikke gjøre noe. Passordet ditt er det samme "
        "til noen følger lenken, og lenken går ut av seg selv.",
    )
    text = f"""{greeting}

{lead}

{url}

{notes[0]}

{notes[1]}

Hilsen Myntos
"""
    return _mail(
        to=to,
        subject="Nytt passord til Myntos",
        text=text,
        html_body=_html(
            greeting=greeting,
            lead=lead,
            button="Velg nytt passord",
            url=url,
            notes=notes,
        ),
    )


def bankid_sign_in_mail(*, to: str, full_name: str | None, url: str) -> Mail:
    """Answer a reset request for an account that signs in with BankID.

    It goes where a reset link would have gone, so that asking cannot tell a
    stranger which addresses are BankID accounts, and so the person who asked
    learns why no link came. It sets nothing: a password on an account BankID
    opened is what BankID's terms forbid, and the only button is the login page.
    """
    greeting = _greeting(full_name)
    lead = (
        "Noen ba om et nytt passord til Myntos-kontoen din. Kontoen logger inn med BankID og "
        "har ikke passord, så det er ingenting å tilbakestille."
    )
    notes = (
        "Vil du heller logge inn med e-post og passord, logg inn med BankID og velg «Bytt til "
        "innlogging med e-post» under «Din konto».",
        "Ba du ikke om dette, trenger du ikke gjøre noe. Ingenting er endret på kontoen.",
    )
    text = f"""{greeting}

{lead}

Logg inn med BankID her: {url}

{notes[0]}

{notes[1]}

Hilsen Myntos
"""
    return _mail(
        to=to,
        subject="Du logger inn på Myntos med BankID",
        text=text,
        html_body=_html(
            greeting=greeting, lead=lead, button="Logg inn med BankID", url=url, notes=notes
        ),
    )


def switch_to_email_mail(*, to: str, full_name: str | None, url: str, minutes: int) -> Mail:
    """Send a BankID account the link that moves it to e-mail and password.

    The "if you did not ask" line differs from the reset's on purpose. Only
    somebody signed in to the account can ask for this one, so a message
    nobody asked for means somebody is, and doing nothing is safe only up to
    the point of telling the person where to sign them out.
    """
    greeting = _greeting(full_name)
    lead = (
        "Du ba om å logge inn på Myntos med e-post og passord i stedet for BankID. Velg "
        "passordet her, så er byttet gjort."
    )
    notes = (
        f"Lenken virker i {minutes} minutter, og bare én gang. Når du har valgt passord, slutter "
        "innlogging med BankID å virke for kontoen, og alle som er logget inn på den, blir "
        "logget ut. Til da logger du inn med BankID som før.",
        "Ba du ikke om dette, ikke følg lenken. Da har noen hatt tilgang til en innlogging på "
        "kontoen din: logg inn med BankID og logg ut alle andre enheter under «Din konto».",
    )
    text = f"""{greeting}

{lead}

{url}

{notes[0]}

{notes[1]}

Hilsen Myntos
"""
    return _mail(
        to=to,
        subject="Bytt til innlogging med e-post i Myntos",
        text=text,
        html_body=_html(greeting=greeting, lead=lead, button="Velg passord", url=url, notes=notes),
    )


def switched_to_bankid_mail(*, to: str, full_name: str | None, url: str) -> Mail:
    """Tell the account's address that it now signs in with BankID, and only so.

    Sent after the fact, because the switch takes the password and every
    other session with it, and the person who did not do it has to hear that
    from somewhere other than a login page that no longer lets them in.
    """
    greeting = _greeting(full_name)
    lead = "Kontoen din logger nå inn med BankID, og bare med BankID."
    notes = (
        "Passordet og eventuell innlogging med Google eller Microsoft er fjernet, og du er "
        "logget ut på alle andre enheter. Vil du tilbake til e-post og passord, velger du «Bytt "
        "til innlogging med e-post» under «Din konto».",
        "Var det ikke deg, har noen vært logget inn på kontoen din og koblet til en BankID. Ta "
        "kontakt med oss med en gang.",
    )
    text = f"""{greeting}

{lead}

{url}

{notes[0]}

{notes[1]}

Hilsen Myntos
"""
    return _mail(
        to=to,
        subject="Du logger nå inn på Myntos med BankID",
        text=text,
        html_body=_html(
            greeting=greeting, lead=lead, button="Se kontoen din", url=url, notes=notes
        ),
    )
