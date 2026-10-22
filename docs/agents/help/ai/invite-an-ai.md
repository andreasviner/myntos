---
title: Invite an AI to work in the books
description: How a person gives their own AI assistant (ChatGPT, Claude, a local model) a key to a company's books in Myntos with «Inviter en KI», including switching keys on the first time. Use when someone asks how to connect ChatGPT or Claude to Myntos, or how to let an AI do their bookkeeping.
pages: /organisasjon/
skill: myntos-connect
---

# Invite an AI to work in the books

A person can let their own AI assistant read and keep the books for one
company: ChatGPT, Claude, Gemini, or a model they run themselves. They make a
**key** in Myntos and paste it into the conversation with the assistant. The
assistant does the rest. The key works for one company only, and the person
decides what it may do on its own and what it has to ask about first.

You are probably that assistant, or an AI explaining this to the person. Do
not ask the person for their password, and do not ask them for a key unless
they want you to work in their books: then this article is how they make one.

## They might ask

- «Hvordan kobler jeg ChatGPT til Myntos?»
- «Kan Claude føre regnskapet mitt i Myntos?»
- «Hvordan gir jeg KI-en tilgang til regnskapet?»
- "How do I let my AI assistant do the bookkeeping in Myntos?"

## Before they start

- Keys must be switched on for the company. The first time, an owner or
  administrator («Eier» or «Administrator») does that, in the same window
  (step 4). Anybody else sees a note saying only an owner or administrator can.
- An account that signs in with BankID cannot make keys yet.
- Whoever makes the key decides what it may do, but never more than their own
  role allows: a «Leser» can only make a key that reads.

## Steps

1. Open the company's settings: the gear icon at the top right, or the company
   name at the top → «Innstillinger for selskapet»
   (`https://myntos.no/organisasjon/`).
2. Find the section «Tilgang», with the card «Medlemmer i …».
3. Press «Inviter en KI», next to «Inviter en kollega». A window opens.
4. **The first time**, the window explains what switching on keys means:
   - the agent can read the whole books, including information about
     customers, suppliers and employees;
   - the model behind the agent decides where that information goes. ChatGPT
     and Claude send it to OpenAI and Anthropic, possibly outside the EEA; a
     model you run yourself keeps it with you. Myntos does not check which
     model is used, so the choice and the responsibility are the company's;
   - you choose for each key what it may do on its own and what it must ask
     you about first;
   - everything a key wants to send out of Myntos, such as the mva-melding
     and the skattemelding to Skatteetaten, waits for your approval every
     time and goes with your own ID-porten login; no key can move money,
     change a bank account number or decide who has access.

   Tick «Jeg har lest dette og vil slå på nøkler for …» and press «Slå på
   nøkler». The window goes straight on to the form.
5. Under «Navn på nøkkelen», give it a name they will recognise, such as
   «Claude på jobb-PC». The name is shown next to every entry the key posts.
6. Under «Hva skal den få gjøre?», choose for «Forberede» and «Bokføre»:
   «Selv», «Spør meg først» or «Nei». Both start on «Spør meg først», which is
   the safe choice: nothing is done until the person approves it. See
   https://myntos.no/llms/help/ai/choose-what-an-ai-may-do.md.
7. Press «Lag nøkkelen».
8. The window shows a short text with the key in it. Press «Kopier» and paste
   it into the conversation with the assistant. The text tells the assistant
   where its instructions are (`https://myntos.no/llms.txt`).
9. Press «Ferdig». The key is not shown again.

The assistant exchanges the key at once, and from then on the copy in the
conversation is worthless. The key must be used within a day.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Inviter en KI» button | They are not signed in to this company, or the page is still loading | Sign in, open the right company |
| «Bare en eier eller administrator kan slå dem på» | Keys are off and their role cannot switch them on | Ask an owner or administrator |
| «en konto som logger inn med BankID kan ikke lage nøkler ennå» | BankID accounts make no keys until BankID's terms are clarified | Use an account that signs in with e-mail |
| «Gi nøkkelen et navn …» | The name was empty | Type a name |
| No choices under «Hva skal den få gjøre?», only «Rollen din lar deg bare lese …» | Their role is «Leser» | The key can only read; ask an administrator for a higher role if more is needed |

## Good to know

- The same key works for a program or script someone wrote: see
  https://myntos.no/llms/help/ai/connect-your-own-program.md.
- The key belongs to the person who made it and works with their permissions
  as they are on each request. If they lose access to the company, the key
  stops.
- Keys stop by themselves after a year, after 30 days unused, and when the
  person's password is reset.
- Everything the key posts is recorded as «<name> via «<key name>»».

## Related

- [Choose what an AI may do](https://myntos.no/llms/help/ai/choose-what-an-ai-may-do.md)
- [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md)
- [See and stop keys](https://myntos.no/llms/help/ai/see-and-stop-keys.md)
- For the assistant itself: [myntos-connect](https://myntos.no/llms/skills/myntos-connect.md)
