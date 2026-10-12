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
   - «Agentens forklaring:» is the AI's own sentence about it. It is the AI's
     claim, so check it against what is actually sent;
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
| «Ingenting venter på godkjenning.» | Nothing waits | The AI may have done it on its own («Selv»), or not asked yet |

## Good to know

- Requests expire after 14 days without an answer and are never carried out.
- A receipt sent with a payment goes into the bilag archive on the new
  voucher when the payment is approved.
- Requests to file the MVA-melding look different: see
  https://myntos.no/llms/help/ai/file-what-an-ai-asks-you-to-file.md.

## Related

- [Decline what an AI asks](https://myntos.no/llms/help/ai/decline-what-an-ai-asks.md)
- [Choose what an AI may do](https://myntos.no/llms/help/ai/choose-what-an-ai-may-do.md)
- For the AI: [myntos-godkjenning](https://myntos.no/llms/skills/myntos-godkjenning.md)
