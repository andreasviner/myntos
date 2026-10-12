---
title: Sign in to Myntos
description: How a person signs in to Myntos on the Logg inn page with e-mail and password, Google, Microsoft or BankID, and what each refusal on that page means. Use when someone cannot get in, is told the account is locked, or asks how to log in.
pages: /logg-inn/
---

# Sign in to Myntos

A person signs in the way their account was set up: e-mail and password,
Google, Microsoft, or BankID. Which buttons appear depends on what this Myntos
offers. The most common confusion is an account that has no password because
it was made with Google, Microsoft or BankID: the password form then always
says the password is wrong, and the answer is the right button, not a reset.

## They might ask

- «Hvordan logger jeg inn i Myntos?»
- «Det står at kontoen er låst»
- «Jeg får "feil e-postadresse eller passord", men passordet er riktig»
- "How do I log in to Myntos?"

## Steps

1. Press «Logg inn» at the top right of myntos.no
   (`https://myntos.no/logg-inn/`). The page is headed «Logg inn».
2. Either press the button for how they signed up: «Logg inn med Google»,
   «Logg inn med Microsoft» or «Logg inn med BankID» (only the ones this
   Myntos offers are shown), and complete the login there.
3. Or, under «eller med e-post», fill in «E-postadresse» and «Passord» and
   press «Logg inn».

They come back to the page they were on their way to, or the front page.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «feil e-postadresse eller passord» | The address has no account, the password is wrong, or the account has no password at all (made with Google, Microsoft or BankID). Myntos gives one answer for all three on purpose. | Try the provider buttons first. Otherwise use «Glemt passordet?» under the form: [Reset a forgotten password](https://myntos.no/llms/help/getting-started/reset-a-forgotten-password.md). |
| «kontoen er midlertidig låst etter for mange forsøk» | Too many wrong passwords (ten) lock the account for 15 minutes. | Wait 15 minutes, or reset the password, which also lifts the lock. |
| «Fyll inn både e-postadresse og passord.» | A field is empty. | Fill in both. |
| An English message such as «value is not a valid email address: …» | The address is mistyped. The message comes from the server untranslated. | Correct the address. |
| «Ingen Myntos-konto er koblet til denne BankID-en ennå. …», with links «Opprett en konto med BankID» and «Din konto» | BankID worked, but no Myntos account uses this BankID. Pressing a login button never creates a BankID account. | If they have an account, sign in with e-mail (or Google/Microsoft) and switch it to BankID: [Switch to BankID](https://myntos.no/llms/help/your-account/switch-to-bankid.md). Otherwise sign up: [Sign up with BankID](https://myntos.no/llms/help/getting-started/sign-up-with-bankid.md). |
| «Kontoen med denne e-postadressen logger inn med BankID. Logg inn med BankID i stedet.» | They pressed Google or Microsoft, and the address belongs to an account that signs in with BankID only. | Press «Logg inn med BankID». |
| «Kontoen logger inn med BankID, og bare med BankID. Passord, Google og Microsoft kan ikke brukes på den.» or «kontoen logger inn med BankID, så logg inn med BankID» | Same: a BankID account. | Press «Logg inn med BankID». |
| «Det finnes allerede en Myntos-konto med denne e-postadressen, og leverandøren har ikke bekreftet at adressen er din. Logg inn på vanlig måte i stedet.» | Google or Microsoft gave an address that has an account, without vouching for it. | Sign in with e-mail and password, then link the provider from «Din konto»: [Link Google or Microsoft](https://myntos.no/llms/help/your-account/link-google-or-microsoft.md). |
| «kontoen er deaktivert» or «Kontoen er deaktivert.» | The account has been switched off. | Nothing to do on the page; write to kontakt@myntos.no. |
| «Innloggingen ble avbrutt.» | They cancelled at the provider. | Try again. |
| «Innloggingen tok for lang tid. Prøv en gang til.» | The provider login took too long. | Try again. |
| «Vi fikk ikke bekreftet kontoen din hos leverandøren. Prøv en gang til.» | The provider's answer could not be checked. | Try again; if it persists, use another way in. |
| «Fikk ikke kontakt med serveren.» | No connection to Myntos. | Check the connection and try again later. |

## Good to know

- **«Glemt passordet?»** appears under the form only when this Myntos sends
  e-mail.
- **Google with a known address.** Signing in with Google (or Microsoft) whose
  verified address matches an existing Myntos account links that provider to
  the account and signs them in.
- **How long it lasts.** A sign-in lasts up to 14 days and ends after 24
  hours without use.
- **Signing out.** «Logg ut» at the top right signs out this device. Other
  devices are signed out from «Din konto»:
  [Sign out other devices](https://myntos.no/llms/help/your-account/sign-out-other-devices.md).
- **Not the ID-porten login.** Connecting a company to Altinn and filing to
  Skatteetaten ask for their own ID-porten login later, whichever way the
  person signs in to Myntos.
- Never give the password to anyone, an AI included. An AI that should work
  in the books gets its own key:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).

## Related

- [Create a Myntos account](https://myntos.no/llms/help/getting-started/create-an-account.md)
- [Reset a forgotten password](https://myntos.no/llms/help/getting-started/reset-a-forgotten-password.md)
