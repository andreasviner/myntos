---
title: Sign up with BankID
description: How a person creates a Myntos account with BankID on the Opprett konto page and finishes it on Fullfør registreringen by giving an e-mail address, and why such an account signs in only with BankID. Use when someone asks how to register with BankID, or why Myntos asks for an e-mail address after BankID.
pages: /registrer/ /fullfor-registrering/
---

# Sign up with BankID

When this Myntos offers BankID, a person can create their account with it
instead of a password. BankID tells Myntos who they are but not how to reach
them, so there is one extra step: typing the e-mail address the account
should have. **An account made with BankID signs in with BankID and nothing
else**: no password, no Google, no Microsoft. That is a rule in BankID's terms
for services like Myntos, not a setting. The person can switch to e-mail and
password later, which ends BankID sign-in for the account.

## They might ask

- «Kan jeg registrere meg med BankID i Myntos?»
- «Hvorfor må jeg skrive inn e-post når jeg har brukt BankID?»
- «Kan jeg ha både BankID og passord?»
- "Can I sign up for Myntos with BankID?"

## Before they start

- BankID sign-in may not be switched on. If «Opprett konto» shows no button
  called «Registrer med BankID», this Myntos does not offer it, and they sign
  up with e-mail or Google/Microsoft instead:
  [Create a Myntos account](https://myntos.no/llms/help/getting-started/create-an-account.md).
- They need an e-mail address that has **no** Myntos account yet. If they
  already have an account, they should sign in to it and switch it to BankID
  instead: [Switch to BankID](https://myntos.no/llms/help/your-account/switch-to-bankid.md).

## Steps

1. Press «Opprett konto» at the top right of myntos.no
   (`https://myntos.no/registrer/`).
2. Read the line under «Registrer med BankID»: «En konto du oppretter med
   BankID, logger bare inn med BankID. Du kan bytte til e-post og passord
   senere, under «Din konto».»
3. Press «Registrer med BankID» and complete BankID.
4. They arrive on «Fullfør registreringen», which says «BankID bekreftet at du
   er …» with their name, and asks «Hvilken e-postadresse skal kontoen ha?»
5. «E-postadresse»: an address they read. Myntos sends a confirmation link
   there.
6. «Navn på organisasjonen (valgfritt)»: leave it empty unless they want
   something there; Myntos never shows it.
7. Press «Opprett konto». They are signed in and land where they started,
   usually the front page.

Finish within half an hour of the BankID login; after that the held signup is
gone and they start again from step 1.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «det finnes allerede en konto med denne e-postadressen. Logg inn på vanlig måte, og koble til BankID under «Din konto». …», with «Er det din konto? Logg inn som vanlig …» under it | The address already has a Myntos account. BankID is never attached to an existing account from here. | If it is theirs, sign in to it and switch it to BankID from «Din konto». Otherwise type a different address; the signup is still held. |
| «denne BankID-en er allerede koblet til en Myntos-konto. Logg inn med BankID.» | They already have an account with this BankID. | Sign in with «Logg inn med BankID» on the sign-in page. |
| «Denne registreringen er utløpt eller allerede fullført. Start på nytt med BankID.», with «Til registreringen» | More than half an hour passed, or the account was already created. | Press «Til registreringen» and start again, or sign in if the account exists. |
| «Skriv inn e-postadressen kontoen skal ha.» | The address field is empty. | Fill it in. |
| «Innloggingen ble avbrutt.» on «Opprett konto» | BankID was cancelled. | Press «Registrer med BankID» again. |
| «Innloggingen tok for lang tid. Prøv en gang til.» | BankID took too long. | Start again. |

## Good to know

- **The name comes from BankID** and cannot be changed on this page.
- **Only one thing from BankID is kept**: a pseudonymous identifier from the
  BankID broker, so Myntos can recognise the person next time, plus the name.
  The fødselsnummer is not asked for.
- **«Glemt passordet?» does not apply.** The account has no password; a reset
  request sends an e-mail saying the account signs in with BankID.
- **No keys for an AI, for now.** A BankID account cannot make a key for an
  AI agent yet (Myntos answers «en konto som logger inn med BankID kan ikke
  lage nøkler ennå …»), because Myntos is still asking BankID whether its
  terms allow it. Someone who wants an AI working in the books should sign up
  with e-mail instead; see
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
- **Signing in with BankID to Myntos is not the ID-porten login** that
  connecting to Altinn and filing to Skatteetaten ask for. Those still ask for
  their own login when the time comes.
- **Coming from an invitation?** The address on «Fullfør registreringen» is
  not filled in for them. Type exactly the address the invitation was sent to,
  or the invitation will refuse the account.

## Related

- [Sign in](https://myntos.no/llms/help/getting-started/sign-in.md)
- [Switch from BankID to e-mail and password](https://myntos.no/llms/help/your-account/switch-from-bankid-to-email.md)
- [Set up the books for your company](https://myntos.no/llms/help/getting-started/set-up-the-books-for-your-company.md)
