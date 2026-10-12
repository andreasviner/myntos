---
title: Switch your account to BankID
description: How a person switches their Myntos account to sign in with BankID using Koble til BankID on the Din konto page, and what they lose by doing so (the password, Google and Microsoft sign-in, other devices' sessions, keys made for AI agents). Use when someone asks how to log in to Myntos with BankID when they already have an account.
pages: /konto/
---

# Switch your account to BankID

An existing account can start signing in with BankID, but it is a switch,
not an addition. **Once BankID is linked, the account signs in with BankID and
nothing else**: the password is deleted, Google and Microsoft sign-in are
removed, every other device is signed out, and any key the person made for an
AI agent stops. That is BankID's rule for services like Myntos (a BankID
login may not be used to set up or restore another way in). The person can
switch back to e-mail and password later, which in turn ends BankID sign-in.

## They might ask

- «Hvordan logger jeg inn med BankID i Myntos når jeg har konto fra før?»
- «Kan jeg bruke både BankID og passord?»
- "How do I switch my Myntos login to BankID?"

## Before they start

- BankID sign-in may not be switched on. If «Slik logger du inn» on «Din
  konto» has no «Koble til BankID» button, this Myntos does not offer it.
- They need their current password, or, for an account without one, to have
  signed in within the last ten minutes.
- **Keys for AI agents stop and cannot be made again** while the account
  signs in with BankID. Someone relying on an AI working through a key should
  stay on e-mail; see [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).

## Steps

1. Open the menu at the top right and press «Din konto»
   (`https://myntos.no/konto/`).
2. In «Slik logger du inn», press «Koble til BankID».
3. Read the warning: «Når du kobler til BankID, kan du bare logge inn med
   BankID. Passordet ditt og innlogging med Google eller Microsoft slutter å
   virke.» and «Du blir også logget ut på alle andre enheter. Vil du tilbake
   senere, kan du bytte til e-post og passord her.»
4. If the account has a password, type it in «Passordet ditt».
5. Tick «Jeg forstår at jeg bare kan logge inn med BankID etterpå.»
6. Press «Fortsett til BankID» and complete BankID. («Avbryt» closes the
   step and changes nothing.)
7. Back on «Din konto», the card says «Du logger nå inn med BankID, og bare
   med BankID. Passordet og innlogging med Google eller Microsoft er fjernet,
   og du er logget ut på alle andre enheter.» An e-mail confirming the change
   goes to the account's address.

Nothing changes until BankID has completed in step 6.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «feil passord» | The password typed in «Passordet ditt» is wrong. Wrong attempts count towards the same lockout as the sign-in page. | Type it again. If forgotten, reset it first. |
| «logg ut og inn igjen for å bytte til BankID. …», with buttons such as «Logg inn på nytt med Google» | The account has no password and the sign-in is older than ten minutes. | Press the «Logg inn på nytt med …» button, then do the switch within ten minutes. |
| «Fortsett til BankID» stays grey | The box is not ticked, or the password field is empty. | Tick the box and type the password. |
| «Denne innloggingen er allerede koblet til en annen Myntos-konto, eller kontoen har en av samme slag fra før.» | That BankID already opens another Myntos account. Nothing was changed on this one. | Sign in to the other account with BankID instead. |
| «Koblingen ble startet fra en annen innlogging enn den du har nå. Start den på nytt fra «Din konto».» | The browser was signed in as someone else when BankID came back. | Start again from «Din konto». |
| «Bytte til BankID må startes fra «Din konto», der du bekrefter det med passordet ditt.» | The switch was started from a link rather than the button. | Use «Koble til BankID» on «Din konto». |
| «innloggingen med BankID kunne ikke starte. Det er en feil hos oss, ikke hos deg.» | A fault on Myntos's side. | Try later. |
| «Innloggingen ble avbrutt.» | BankID was cancelled. Nothing changed. | Start again if they still want to. |

## Good to know

- **«Glemt passordet?» stops applying.** A BankID account has no password; a
  reset request only mails a reminder that it signs in with BankID.
- **The name stays as it is.** Nothing about the account becomes
  "BankID-verified"; the BankID login proves nothing to Skatteetaten, and
  filings still ask for their own ID-porten login.
- **Only a pseudonymous identifier is stored** from BankID; the fødselsnummer
  is not asked for.

## Related

- [Switch from BankID to e-mail and password](https://myntos.no/llms/help/your-account/switch-from-bankid-to-email.md)
- [Sign out other devices](https://myntos.no/llms/help/your-account/sign-out-other-devices.md)
