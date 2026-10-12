---
title: Reset a forgotten password
description: How a person gets a reset link on the Glemt passordet page and chooses a new password on Velg nytt passord in Myntos, and why everyone is signed out afterwards. Use when someone has forgotten their password, is locked out, or asks why their AI key stopped after a reset.
pages: /glemt-passord/ /nytt-passord/ /logg-inn/
---

# Reset a forgotten password

A person who has forgotten their password asks for a link by e-mail and
chooses a new one with it. **Afterwards every device is signed out, this one
included, and every key the person made for an AI agent stops.** That is on
purpose: people reset because they fear someone else knows the old password,
and a session or key left alive would keep that someone in.

## They might ask

- «Jeg har glemt passordet til Myntos»
- «Hvordan tilbakestiller jeg passordet?»
- «Jeg får ikke e-posten for nytt passord»
- "I forgot my Myntos password"

## Before they start

- This only works when this Myntos sends e-mail. If it does not, «Glemt
  passordet?» is missing from the sign-in page.
- An account that signs in with **BankID** has no password, and a reset
  request only sends a reminder of that. See Good to know.

## Steps

1. On «Logg inn» (`https://myntos.no/logg-inn/`), press «Glemt passordet?»
   under the form. Or open `https://myntos.no/glemt-passord/` directly.
2. Under «Glemt passordet?», fill in «E-postadresse» with the address they
   sign in with, and press «Send lenke».
3. The page answers «Har … en konto hos Myntos, er e-posten på vei. Lenken i
   den virker i en halvtime.» It says the same whether or not the address has
   an account.
4. Open the e-mail and press the link. It opens «Velg nytt passord»
   (`https://myntos.no/nytt-passord/`), which warns «Etterpå blir alle som er
   logget inn på kontoen logget ut.»
5. Type the new password in «Nytt passord» (the hint gives the minimum
   length) and press «Lagre passordet».
6. «Passordet er byttet. Logg inn med det nye.» Press «Logg inn» and sign in
   with the address and the new password.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No e-mail after a few minutes | The page itself says what to check: «Ser du ingenting etter noen minutter, sjekk søppelpost, og at adressen er den du registrerte deg med.» Also, after several requests in one hour no more mails are sent. | Check spam and the address. Wait an hour if they asked many times. |
| «Denne lenken virker ikke lenger. Den kan være brukt, for gammel, eller erstattet av en nyere.», with «Be om en ny lenke» | The link was used, is older than 30 minutes, or a newer one was sent. | Press «Be om en ny lenke» and use the newest e-mail. |
| «passordet må være minst 10 tegn» | Too short. The link is not used up. | Choose a longer password and press «Lagre passordet» again. |
| «Denne installasjonen av Myntos sender ikke e-post, så passordet kan ikke tilbakestilles herfra. Kontakt den som drifter den.» | This Myntos sends no e-mail. | Contact whoever runs it. |
| «Skriv e-postadressen du logger inn med.» | The field is empty. | Fill it in. |
| «kontoen logger inn med BankID og kan ikke få passord med denne lenken. …» | The account signs in with BankID. | Sign in with BankID; see Good to know. |

## Good to know

- **Everyone is signed out** after the new password is saved, on every
  device, and no new sign-in is made automatically: the person signs in again
  with the new password.
- **Keys for AI agents stop.** Whoever knew the old password could have made
  one. If the person had given an AI a key, they make a new one afterwards:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
- **A lockout ends** when the password is reset, and the address counts as
  confirmed.
- **Accounts made with Google or Microsoft** get a link too. Following it
  sets a first password, and the provider keeps working beside it.
- **BankID accounts** get an e-mail saying the account signs in with BankID,
  with a link to the sign-in page and no reset link. The page says so in
  advance: «Logger du inn med BankID, har kontoen ikke passord. …» To use a
  password instead, they switch:
  [Switch from BankID to e-mail and password](https://myntos.no/llms/help/your-account/switch-from-bankid-to-email.md).
- The reset link is a password for half an hour. Nobody, an AI included,
  should be given it or the new password.

## Related

- [Sign in](https://myntos.no/llms/help/getting-started/sign-in.md)
- [Change or set your password](https://myntos.no/llms/help/your-account/change-or-set-your-password.md)
