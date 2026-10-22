---
title: Choose what an AI may do on its own
description: What the choices «Selv», «Spør meg først» and «Nei» for «Forberede» and «Bokføre» mean when making a key for an AI or a program in Myntos, and which to pick. Use when someone asks how much an AI may do in Myntos, or whether an AI can post or send things without asking.
pages: /organisasjon/
skill: myntos-godkjenning
---

# Choose what an AI may do on its own

When a person makes a key («Inviter en KI»), they choose for two rights how
much the AI or program may do without them:

| Right | Covers | Can it be undone? |
| --- | --- | --- |
| «Lese» | Seeing the whole books | Always on, for every key |
| «Forberede» | Draft invoices, customers and suppliers, matching bank lines, the chart of accounts | Yes: changed or deleted again |
| «Bokføre» | Posting payments, issuing invoices and credit notes, importing bank statements, closing a year | No: only corrected with a new entry |
| «Sende til Skatteetaten» | Checking and sending the MVA-melding and the skattemelding | Never by a key on its own («Aldri selv»): a key that may post («Selv» or «Spør meg først» for «Bokføre») can ask, and each one waits for the person on «Godkjenning», with their ID-porten login |

For «Forberede» and «Bokføre» the choices are:

- «Selv»: the AI does it straight away.
- «Spør meg først»: the AI's request is stored
  and waits on the page «Godkjenning» until the person approves it. Nothing
  happens before that.
- «Nei»: the AI cannot do it at all.

## They might ask

- «Kan KI-en bokføre uten at jeg godkjenner det?»
- «Hvordan bestemmer jeg hva agenten får lov til?»
- «Kan agenten sende mva-meldingen for meg?»

## Which to choose

- **Not sure:** leave both on «Spør meg først», which is where the form starts.
  The AI can do all the work, and the person presses «Godkjenn» for each thing.
- **Trust it with drafts, check the postings:** «Forberede»: «Selv»,
  «Bokføre»: «Spør meg først».
- **Let it do everything:** «Selv» for both. Whatever it sends to
  Skatteetaten still waits for the person, every time.
- **Only answer questions:** «Nei» for both.

«Bokføre» can never be freer than «Forberede»: choosing «Spør meg først» for
«Forberede» moves «Bokføre» to «Spør meg først» or «Nei», and «Selv» is
greyed out.

## Good to know

- The choice is made once, when the key is made. To change it, make a new key
  and stop the old one (https://myntos.no/llms/help/ai/see-and-stop-keys.md).
- A key never does more than the person who made it may do: a «Medlem» cannot
  make a key that changes the chart of accounts, because they cannot either.
- Proposals that store nothing (reading a receipt to suggest an entry,
  suggesting invoice lines) run at once for a key that may prepare, also with
  «Spør meg først», because there is nothing to approve.
- The AI is told to show the person what it will post before it posts, even
  with «Selv».

## Related

- [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md)
- [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md)
- [What a key can never do](https://myntos.no/llms/help/ai/what-a-key-can-never-do.md)
