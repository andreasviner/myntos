---
title: See where you are signed in, and sign out other devices
description: How a person sees every device signed in to their Myntos account under Innloggingene dine on the Din konto page, signs out one of them or all the others, and signs out the device they are on. Use when someone asks how to log out of Myntos on another computer or phone, or thinks someone else is using their account.
pages: /konto/
---

# See where you are signed in, and sign out other devices

«Din konto» lists every device where the account is signed in right now,
named coarsely («Firefox på Linux»), with when it signed in and when it was
last used. Any of them can be signed out from there. A person who does not
recognise one should sign it out and then change the password, because
whoever is on that device may know it.

## They might ask

- «Hvordan logger jeg ut av Myntos på en annen PC?»
- «Jeg tror noen andre er logget inn på kontoen min»
- «Hvordan logger jeg ut?»
- "How do I log out of Myntos on all devices?"

## Steps

1. Open the menu at the top right and press «Din konto»
   (`https://myntos.no/konto/`).
2. Under «Innloggingene dine», each row is one device: its browser and
   system (or «Ukjent nettleser»), «Logget inn …, sist brukt …», and «Denne
   enheten» on the one they are using.
3. To sign out one device, press «Logg ut» on its row.
4. To sign out all the others at once, press «Logg ut alle andre enheter».
   The page says «Alle andre enheter er logget ut.» The device they are on
   stays signed in.
5. To sign out the device they are on, press «Logg ut» at the top right of
   any page.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| A device they do not recognise | Possibly someone else with their password. | Sign it out, then change the password: [Change or set your password](https://myntos.no/llms/help/your-account/change-or-set-your-password.md). A password reset signs out every device anyway. |
| «Vi fikk ikke logget ut enheten.» or «Vi fikk ikke logget ut de andre enhetene.» | The request failed. | Try again. |
| No «Logg ut alle andre enheter» button | No other device is signed in. | Nothing to do. |

## Good to know

- **Sign-ins end by themselves** after 14 days, or after 24 hours unused.
- **Keys for AI agents are not sessions.** Signing out devices does not stop
  a key the person made for an AI; keys are stopped on the company's
  settings page under «Tilgang». See
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
- **What is shown is less than what is stored.** The list shows browser and
  system only. Myntos keeps the IP address and full browser string for
  investigating incidents, and never shows them on screen.
- On a BankID account the hint says only to sign out what they do not
  recognise; there is no password to change.

## Related

- [Change or set your password](https://myntos.no/llms/help/your-account/change-or-set-your-password.md)
- [Sign in](https://myntos.no/llms/help/getting-started/sign-in.md)
