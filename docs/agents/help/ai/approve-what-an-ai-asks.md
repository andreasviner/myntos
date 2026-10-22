---
title: Approve what an AI asks to do
description: How a person approves a request their AI assistant or program made in Myntos, on the page «Godkjenning»: what it shows, what «Godkjenn» does, and what happens afterwards. Use when someone asks where to approve what their AI did, or why their AI says something is waiting for them.
pages: /godkjenning/
skill: myntos-godkjenning
---

# Approve what an AI asks to do

A key with «Spør meg først» does not post or change anything itself. Each
thing it asks for waits on the page «Godkjenning» until the person approves
or declines it. Approved, it is carried out exactly as the AI sent it, through
every check Myntos makes, and recorded as done by the person via the key.

## They might ask

- «Agenten sier noe venter på godkjenning. Hvor godkjenner jeg det?»
- «Hvordan godkjenner jeg det KI-en har gjort?»
- "Where do I approve what my AI did in Myntos?"

## Before they start

Only the person who made the key can approve what it asks. An owner or
administrator sees colleagues' requests too, and may decline them, but not
approve them.

## Steps

1. Open «Godkjenning» in the top menu (`https://myntos.no/godkjenning/`). The
   menu item appears when the company has keys switched on, with a number when
   something waits. The AI usually gives a direct link too.
2. Under «Venter på deg», each request is a card headed ««<key name>» vil …»,
   for example ««Claude» vil føre en betaling».
3. Read the card:
   - «Dette kan ikke angres når det er gjort …» marks something final, such as
     posting or issuing an invoice;
   - «Når du godkjenner, sendes dette til Skatteetaten med din egen
     ID-porten-innlogging, som om du trykket på knappen selv.» marks a check
     or a filing with Skatteetaten (««Claude» vil sende skattemeldingen til
     Skatteetaten»). If they are not logged in with ID-porten, the card shows
     «Logg inn med ID-porten» instead of «Godkjenn»: they log in, come back
     to the card, and approve;
   - «Gjelder» names what the request acts on, read by Myntos from the books:
     the voucher a reversal reverses («Bilag 2026-0014, 14.03.2026:
     Kontorrekvisita, 1 250,00 kr»), the invoice it issues, the account it
     changes. «… finnes ikke i regnskapet» means the record is not there, and
     the request will be refused;
   - «Agentens forklaring:» is the AI's own sentence about it. It is the AI's
     claim, so check it against «Gjelder» and what is actually sent;
   - the files sent with it (a receipt, say): press a file name to open it;
   - «Det som blir sendt»: open it to see every field the request carries;
   - «Se …» links to the page where the result will show.
4. Press «Godkjenn». The button says «Utfører …» while it runs, then the card
   says «Utført.», or «Myntos avviste det: …» if Myntos refused it (a closed
   year, a missing account), with the reason.
5. Done requests move down to «Avgjort».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Venter på <name>.» and no «Godkjenn» | It is a colleague's key | Only that person can approve it |
| «Myntos avviste det: …» | The request was carried out and refused by Myntos's own checks | Tell the AI; it can fix it and ask again |
| «forespørselen ventet for lenge og har gått ut» | Not decided within 14 days | Ask the AI to ask again if still wanted |
| «nøkkelen som ba om dette er stoppet» | The key was stopped since | Nothing can be approved from a stopped key |
| «dette sendes til Skatteetaten med din egen ID-porten-innlogging. Logg inn med ID-porten, og godkjenn så igjen.» | The ID-porten login is missing or has expired | Press «Logg inn med ID-porten» on the card, then «Godkjenn». The request still waits |
| «forespørselen har fått andre filer enn dem du så. Se over den igjen før du godkjenner.» | The AI added a file to the request after the card was shown | The card is shown again with the new file: open it, then press «Godkjenn» again if it is right |
| «Ingenting venter på godkjenning.» | Nothing waits | The AI may have done it on its own («Selv»), or not asked yet |

## Good to know

- Requests expire after 14 days without an answer and are never carried out.
- A receipt sent with a payment goes into the bilag archive on the new
  voucher when the payment is approved.
- Anything an AI wants to send out of Myntos waits here every time, even
  from a key allowed to post on its own («Selv»).
- Checks and filings with Skatteetaten, and requests to file them yourself:
  https://myntos.no/llms/help/ai/file-what-an-ai-asks-you-to-file.md.

## Related

- [Decline what an AI asks](https://myntos.no/llms/help/ai/decline-what-an-ai-asks.md)
- [Choose what an AI may do](https://myntos.no/llms/help/ai/choose-what-an-ai-may-do.md)
- For the AI: [myntos-godkjenning](https://myntos.no/llms/skills/myntos-godkjenning.md)
