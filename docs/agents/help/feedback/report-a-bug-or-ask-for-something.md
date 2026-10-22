---
title: Report a bug or ask for something
description: How a person reports a bug, suggests an improvement or asks for a feature on the Tilbakemelding page in Myntos, anonymously and without signing in, and how an AI can write the report and hand them a filled-in link. Use when someone says something in Myntos does not work, or wishes it did something it does not.
pages: /tilbakemelding/ /funksjoner/
skill: myntos-tilbakemelding
---

# Report a bug or ask for something

Anybody can tell Myntos that something is wrong or missing, signed in or not.
Myntos does not store who sent it: not the account, not the company, not the
IP address. The one thing the person must not get wrong: after sending, they
get a link, and **that link is the only way to see the answer** or take the
report back. Nobody at Myntos can find it for them.

## They might ask

- «Hvordan melder jeg en feil i Myntos?»
- «Kan jeg ønske meg en funksjon i Myntos?»
- «Hvor sier jeg fra om at noe ikke virker?»
- "How do I report a bug in Myntos?"
- "Can I suggest a feature to Myntos?"

## Before they start

Nothing. No account, no role, no sign-in.

## Steps

1. Open the page: «Meld feil eller ønsker» in the footer of every page, the
   link «Si fra» at the bottom of «Funksjoner», or straight to
   `https://myntos.no/tilbakemelding/`. The heading is «Si fra om feil,
   forbedringer og ønsker».
2. Under «Send tilbakemelding», choose what it is:
   - «Feil»: «Noe virker ikke som det skal»
   - «Forbedring»: «Noe virker, men kunne vært bedre»
   - «Ønske»: «Noe Myntos ikke gjør ennå»
3. Write it in «Hva skjedde, eller hva ønsker du deg?»: what they pressed and
   what they expected. At least 10 characters, at most 5000. **No names,
   amounts or anything else from the books**; Myntos cannot ask them for more
   afterwards, so the text has to stand on its own.
4. «Hvilken side gjaldt det? (valgfritt)» is filled in with the page they came
   from, when they followed a link from Myntos. They may change or clear it.
5. Press «Send tilbakemelding». The page says «Takk! Tilbakemeldingen er
   sendt.» and shows a link.
6. Keep the link: «Kopier lenken» copies it. «Send en til» opens an empty
   form.

## If you are an AI helping them, write it for them

You cannot send it from a chat without HTTP requests, but you can write the
report and give the person a link that opens the form filled in. They read it
and press «Send tilbakemelding» themselves:

`https://myntos.no/tilbakemelding/#type=feil&tekst=<the text, URL-encoded>&side=/faktura/`

- `type` is `feil`, `forbedring` or `onske`.
- `tekst` is the report, URL-encoded (spaces as `%20`). Keep it free of names
  and amounts, for the reason above.
- `side` is optional: the page it happened on.

The part after `#` never reaches a server, so nothing is sent until they press
the button. An agent that can make HTTP requests can send it directly instead:
[myntos-tilbakemelding](https://myntos.no/llms/skills/myntos-tilbakemelding.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Skriv litt mer, minst 10 tegn, så vi forstår hva det gjelder.» | The text is too short | Write a sentence or two more |
| «Teksten kan være høyst 5000 tegn. …» | Too long | Shorten it, the most important first |
| «Det har kommet mange tilbakemeldinger den siste timen. Prøv igjen om en time.» | A cap on everybody's feedback together, not on them | Try again in an hour |
| «Vi har mange tilbakemeldinger vi ikke har lest ennå, …» | Myntos has too much unread | Try again in a few days, or write to kontakt@myntos.no |

## Good to know

- Only the person running Myntos reads the text. If Myntos shows a report
  publicly, it is in Myntos's own words, never theirs: see
  [See what others have reported](https://myntos.no/llms/help/feedback/see-what-others-have-reported.md).
- What is stored and for how long is in «Hva vi lagrer» on the page and in the
  personvernerklæring, section e3 (`https://myntos.no/personvern/#tilbakemelding`).
- Something urgent, or about their own account or data, goes to
  kontakt@myntos.no instead, where they can be answered by e-mail.

## Related

- [Follow up on what you reported](https://myntos.no/llms/help/feedback/follow-up-on-what-you-reported.md)
- [See what others have reported](https://myntos.no/llms/help/feedback/see-what-others-have-reported.md)
- An agent can report directly through the API:
  [myntos-tilbakemelding](https://myntos.no/llms/skills/myntos-tilbakemelding.md)
