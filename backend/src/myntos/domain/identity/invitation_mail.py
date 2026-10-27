"""The invitation email: what it says, and why it says it.

Kept apart from both the transport (:mod:`myntos.core.mail`) and the endpoint
that triggers it, so the wording can be read and asserted on its own. Every
message is Norwegian, addressed to a person who may never have heard of Myntos,
and it answers the three questions such a person actually has: who is asking,
what they are being let into, and what happens if they do nothing.

The link is the whole message. Everything else is context for deciding whether
to follow it, which is why the role is spelled out in words rather than as
``member``, and why the expiry is a date rather than "7 days".

Three things beyond the words carry weight here.

**The mark.** An invitation is often the first thing Myntos ever says to
somebody, and a message with no sender identity on it reads like the phishing it
is shaped exactly like: a stranger's name, a company you may not know, and
a link. The logo travels inside the message rather than being fetched from a
server, for the reasons :mod:`myntos.core.brand` sets out, and the word "Myntos"
sits beside it as text so a client that blocks images still shows a sender.

**The name.** When the inviter addressed this to a person, picked out of the
company's own board list say, the message greets them by that name. It is the
cheapest signal there is that a human chose to send this, and it costs nothing
when absent: the greeting simply is not there.

**The shape.** One lead sentence, one button, then the two facts a person needs
in order to act on it (when the link dies, which address it belongs to) as a
quiet strip, then the reassurance, then the raw URL last of all. The first
version set every one of those at the same weight and bolded the entire role
clause, so a message with five things to say read as a wall with a button
somewhere in the middle. The raw link was the worst of it and cannot be removed,
because a client that strips the button has to leave something clickable: it
sits at the end now, in the faintest grey the message uses, instead of in brand
green halfway up.

**The inviter's address is in the text, not in Reply-To.** A recipient unsure
whether this is genuine needs somebody to ask, and the first version made that
the Reply-To header. A Reply-To on another domain than From is how a phishing
message is built, and spam filters score it as one. Every invitation that
carried one in October 2026 was refused by Cloudflare at its threshold, while
the account mails, identical in layout and without one, went through: three
messages against three, a correlation and not a proof, and a test invitation
whose Reply-To was the recipient's own address did arrive. The header was
worth too little to keep on that evidence. Written in the text, the address
still reaches the reader, and a reply to the message goes to the deployment's
own ``MAIL_REPLY_TO`` like every other one.
"""

from __future__ import annotations

import html
from datetime import datetime

from myntos.core.brand import INK, LOGO_CID, LOGO_WIDTH, logo_png
from myntos.core.mail import InlineImage, Mail
from myntos.domain.identity.models import OrganizationRole

# What each role may do, in the words the interface uses. Kept beside the mail
# rather than imported from the frontend's copy for the obvious reason: an
# invitation that promises more than the role carries is worse than one that
# says nothing, so this list is checked against
# myntos.domain.identity.access.permissions_for in the tests.
ROLE_WORDS: dict[OrganizationRole, tuple[str, str]] = {
    OrganizationRole.OWNER: ("eier", "kan alt: regnskap, selskaper, medlemmer og abonnement"),
    OrganizationRole.ADMIN: ("administrator", "kan føre regnskap og styre selskaper og medlemmer"),
    OrganizationRole.MEMBER: ("medlem", "kan føre bilag og lage fakturaer"),
    OrganizationRole.VIEWER: ("leser", "kan se regnskapet, men ikke endre noe"),
}

# The greys the message is built from, written out because an email has no
# stylesheet to resolve a token against. Three and no more: everything that is
# not the lead sentence or the button is supporting text, and giving each piece
# of it its own shade is how the first version turned into a wall.
_QUIET = "#5f6661"
_FAINT = "#8a8f8b"
_RULE = "#e8e8e3"
# The strip under the button. Barely off-white: it groups two facts without
# becoming a second thing to look at.
_WASH = "#f6f7f6"

_MONTHS = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)


def _norwegian_date(moment: datetime) -> str:
    """``3. september 2026``. Read by a person, so not ISO 8601."""
    return f"{moment.day}. {_MONTHS[moment.month - 1]} {moment.year}"


def _first_name(full_name: str | None) -> str | None:
    """The name to greet somebody by, or nothing.

    First name only: "Hei Andreas" is how a colleague writes, and "Hei Andreas
    Edvardsen Lindeman" is how a register does. A single-word name is its own
    first name, and anything blank is no name at all.
    """
    if full_name is None:
        return None
    first = full_name.strip().split(" ")[0] if full_name.strip() else ""
    return first or None


def _detail_row(label: str, value: str, *, last: bool = False) -> str:
    """One label/value line in the strip under the button.

    A table rather than two divs: a table cell is the only box an email client
    can be relied on to place, and the label column has to hold its width
    beside a value long enough to wrap, which an address is.
    """
    padding = "0" if last else "0 0 7px"
    return (
        f'<tr><td style="padding:{padding};font-size:0.82rem;color:{_FAINT};'
        'white-space:nowrap;vertical-align:top;width:6.5rem;">'
        f"{label}</td>"
        f'<td style="padding:{padding};font-size:0.82rem;color:{_QUIET};'
        'vertical-align:top;word-break:break-word;">'
        f"{value}</td></tr>"
    )


def _ask_clause(inviter: str | None, address: str) -> str:
    """Whom to ask whether this is genuine, by name when there is one."""
    who = "avsenderen" if inviter is None or inviter.strip() == "" else inviter.strip()
    return f"Er du usikker på om invitasjonen er ekte, kan du spørre {who} på {address}."


def _inviter_clause(inviter: str | None, company: str) -> str:
    """Who is asking, when we know, and an honest sentence when we do not."""
    if inviter is None or inviter.strip() == "":
        return f"Du er invitert til å føre regnskapet for {company} på Myntos."
    return f"{inviter.strip()} har invitert deg til å føre regnskapet for {company} på Myntos."


def invitation_mail(
    *,
    to: str,
    company: str,
    role: OrganizationRole,
    invite_url: str,
    expires_at: datetime,
    inviter_name: str | None = None,
    inviter_email: str | None = None,
    recipient_name: str | None = None,
    resent: bool = False,
) -> Mail:
    """Compose the invitation.

    ``inviter_email`` is named in the text as the address to ask, when the
    inviter has confirmed it; never as the Reply-To (see the module docstring).

    ``recipient_name`` is the person the invitation was addressed to, when the
    inviter named one. ``resent`` says so in one line rather than letting a
    second identical message look like a system that lost the first.
    """
    role_word, role_blurb = ROLE_WORDS[role]
    opening = _inviter_clause(inviter_name, company)
    deadline = _norwegian_date(expires_at)
    greeting = _first_name(recipient_name)
    ask = None if inviter_email is None else _ask_clause(inviter_name, inviter_email)
    # A resend is the same invitation with a new link, and saying so is what
    # keeps the second copy from reading as a duplicate somebody should worry
    # about. The old link stopped working when this one was made, which the
    # recipient needs to know if they still have the first message open.
    reminder = (
        "Dette er invitasjonen sendt på nytt. Den forrige lenken virker ikke lenger, så bruk denne."
        if resent
        else None
    )

    subject = (
        f"Påminnelse: du er invitert til {company} på Myntos"
        if resent
        else f"Du er invitert til {company} på Myntos"
    )

    text = "".join(
        part
        for part in (
            f"Hei {greeting},\n\n" if greeting is not None else "",
            f"{opening}\n\n",
            f"{reminder}\n\n" if reminder is not None else "",
            f"""Myntos er et norsk regnskapsprogram. Rollen din blir {role_word}, som betyr at du
{role_blurb}.

Slik blir du med:

{invite_url}

Lenken gjelder bare deg, og bare til {deadline}. Du trenger en Myntos-konto med
denne e-postadressen ({to}) for å bruke den. Har du ingen konto, kan du opprette
en når du følger lenken.

Var dette uventet, trenger du ikke gjøre noe. Invitasjonen går ut av seg selv,
og ingen får tilgang til noe før du takker ja.

""",
            f"{ask}\n\n" if ask is not None else "",
            "Hilsen Myntos\n",
        )
    )

    # Everything interpolated is escaped: a company is named by whoever
    # created it, and a name containing a tag would otherwise be markup.
    safe_opening = html.escape(opening)
    safe_role_word = html.escape(role_word)
    safe_role_blurb = html.escape(role_blurb)
    safe_url = html.escape(invite_url, quote=True)
    safe_to = html.escape(to)
    safe_deadline = html.escape(deadline)
    safe_company = html.escape(company)
    safe_greeting = None if greeting is None else html.escape(greeting)
    safe_reminder = None if reminder is None else html.escape(reminder)
    safe_ask = None if ask is None else html.escape(ask)

    greeting_markup = (
        ""
        if safe_greeting is None
        else f'<p style="margin:0 0 8px;font-size:0.95rem;color:{_QUIET};">Hei {safe_greeting},</p>'
    )
    # Amber and left-ruled rather than the wash used below it: a resend is a
    # notice about the message itself, and it has to read as different from the
    # strip of facts about the invitation.
    reminder_markup = (
        ""
        if safe_reminder is None
        else (
            '<p style="margin:0 0 22px;padding:11px 14px;background:#fdf6e8;'
            "border-left:3px solid #d99b28;border-radius:0 6px 6px 0;"
            f'font-size:0.88rem;color:#6b5324;">{safe_reminder}</p>'
        )
    )
    # In the reassurance's own voice and weight, directly under it: it is the
    # same answer to the same worry, with somebody to ask added.
    ask_markup = (
        ""
        if safe_ask is None
        else f'<p style="margin:0 0 22px;font-size:0.85rem;color:{_QUIET};">{safe_ask}</p>'
    )

    # The two facts that decide whether the link is usable, as label and value
    # rather than as a sentence each. A person checks "is this me, and am I in
    # time" by scanning, and prose makes them read to find out.
    details = "".join(
        (
            _detail_row("Gjelder til", safe_deadline),
            _detail_row("Din e-post", safe_to, last=True),
        )
    )

    # Inline styles and a single column: an email client is not a browser, and
    # anything cleverer than this renders differently in each one. Tables do the
    # layout, because a table cell is the one box every client places correctly.
    #
    # The mark is `cid:`, never a URL, and it carries alt text that is the brand
    # name, so a blocked image reads as "Myntos" twice rather than as a grey box
    # next to a stranger's link.
    html_body = f"""<!doctype html>
<html lang="nb">
  <head>
    <meta charset="utf-8" />
    <!-- Declared light so a client in dark mode restyles the card instead of
         inverting it. An inverted white card puts the mark on a background it
         was never drawn against, and turns the button's green to mud. -->
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
      {greeting_markup}
      <p style="margin:0 0 10px;font-size:1.1rem;font-weight:600;line-height:1.4;
                letter-spacing:-0.01em;color:{INK};">{safe_opening}</p>
      <p style="margin:0 0 22px;font-size:0.95rem;color:{_QUIET};">
        Rollen din blir <strong style="color:{INK};font-weight:600;">{safe_role_word}</strong>, som
        betyr at du {safe_role_blurb}.
      </p>
      {reminder_markup}
      <p style="margin:0 0 22px;">
        <a href="{safe_url}"
           style="display:inline-block;background:#1b6b4a;color:#fff;text-decoration:none;
                  padding:13px 22px;border-radius:8px;font-weight:600;font-size:1rem;"
           >Åpne regnskapet for {safe_company}</a>
      </p>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             bgcolor="{_WASH}"
             style="margin:0 0 20px;border-collapse:collapse;background:{_WASH};
                    border-radius:8px;">
        <tr>
          <td style="padding:13px 15px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
                   style="border-collapse:collapse;">
              {details}
            </table>
          </td>
        </tr>
      </table>
      <p style="margin:0 0 10px;font-size:0.85rem;color:{_QUIET};">
        Myntos er et norsk regnskapsprogram. Lenken gjelder bare deg, og krever en konto på
        denne e-postadressen. Har du ingen, oppretter du en når du følger lenken.
      </p>
      <p style="margin:0 0 {"10px" if safe_ask is not None else "22px"};font-size:0.85rem;
                color:{_QUIET};">
        Var dette uventet, trenger du ikke gjøre noe. Invitasjonen går ut av seg selv, og ingen
        får tilgang til noe før du takker ja.
      </p>
      {ask_markup}
      <p style="margin:0;padding-top:15px;border-top:1px solid {_RULE};
                font-size:0.78rem;color:{_FAINT};">
        Virker ikke knappen, kopier denne lenken inn i nettleseren:<br />
        <a href="{safe_url}"
           style="color:{_FAINT};word-break:break-all;">{safe_url}</a>
      </p>
    </div>
  </body>
</html>
"""

    return Mail(
        to=to,
        subject=subject,
        text=text,
        html=html_body,
        images=(InlineImage(cid=LOGO_CID, data=logo_png()),),
    )
