---
title: See who recorded a payment
description: How a person sees who registered each payment in the register on the Betaling page in Myntos: «Ført av» under «Vis info», the mark under the description when more than one person or an AI agent books in the company, «via «…»» for an AI's key, and «Ukjent». Use when someone asks who booked a payment, why some lines say «Ført av», or how to tell what their AI registered.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# See who recorded a payment

Myntos records who registered every payment, and shows it in the register on
the «Betaling» page. On books one person keeps alone, that would be the same
name on every line, so it is shown under the description only when more than
one hand has booked in the register. A payment an AI agent registered with a
key shows the person who made the key, followed by the key's name.

## They might ask

- «Hvem førte denne betalingen?»
- «Hva betyr «Ført av» under beskrivelsen?»
- «Hvordan ser jeg hva KI-en min har ført?»
- "How do I see who recorded a transaction in Myntos?"

## Before they start

Anyone with access to the company can see it, whatever their role.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. In the register, click «Vis info» on the payment. «Ført av» names who
   registered it. This is always shown there.
3. When more than one person, or a person and an AI agent, have registered
   payments in the list, every line also says «Ført av …» under its
   description, so it can be read without opening the details.
4. How the name reads:
   - «Ført av Kari Nordmann»: Kari registered it in Myntos.
   - «Ført av Kari Nordmann via «Claude»»: it came from an AI agent using a
     key Kari made and named «Claude». Kari is the one who registered it in
     the law's sense; the key is how.
   - «Ført av Ukjent»: no author was recorded, which happens for payments
     registered before Myntos began recording it.

## Good to know

- A correction and a reversal record the person who made them, not whoever
  registered the original payment.
- The author cannot be changed, not even by an administrator.
- A person who later deletes their Myntos account is still named on the
  payments they registered, because the books must be able to say who
  registered each entry.
- An AI agent can register payments only if someone gave it a key that may
  post, and may then have to wait for that person's approval under
  «Godkjenning»:
  [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md).

## Related

- [Read the payment register](https://myntos.no/llms/help/payments/read-the-payment-register.md)
- [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md)
- An agent with a key can read the same through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
