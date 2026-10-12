---
title: Switch from BankID to e-mail and password
description: How a person moves a Myntos account that signs in with BankID to e-mail and password, with Bytt til innlogging med e-post on the Din konto page and the mailed link that opens Bytt til innlogging med e-post. Use when someone wants to stop using BankID for Myntos, wants a password, or wants Google or Microsoft sign-in on a BankID account.
pages: /konto/ /bytt-til-epost/
---

# Switch from BankID to e-mail and password

An account that signs in with BankID can move to e-mail and password. It is
done in two parts: the person asks for a link on «Din konto», and the link,
mailed to the address already on the account, is where they choose the
password. **When the password is chosen, BankID stops working for the account
and every device is signed out.** Until then nothing has changed, and BankID
works as before.

## They might ask

- «Jeg vil heller logge inn med passord enn BankID»
- «Hvordan slutter jeg å bruke BankID i Myntos?»
- «Kan jeg koble Google til en BankID-konto?»
- "How do I switch my Myntos login from BankID to a password?"

## Before they start

- They must be signed in with BankID.
- They must be able to read mail at the address on the account (shown under
  their name at the top of «Din konto»). The link goes there and nowhere
  else.
- This Myntos must send e-mail. If it does not, «Din konto» says «Denne
  installasjonen sender ikke e-post, så kontoen kan ikke bytte fra BankID
  herfra.», and the account stays on BankID.

## Steps

1. Open the menu at the top right and press «Din konto»
   (`https://myntos.no/konto/`).
2. In «Slik logger du inn», press «Bytt til innlogging med e-post».
3. Read «Når du bytter til e-post, slutter innlogging med BankID å virke for
   denne kontoen.» and the line naming the address the link goes to.
4. Tick «Jeg forstår at BankID slutter å virke for kontoen når jeg har valgt
   passord.» and press «Send lenken». («Avbryt» closes it.)
5. The card says «Lenken er sendt til … Den virker i en halvtime. Til du har
   valgt passord med den, logger du inn med BankID som før.»
6. Open the e-mail and press the link. It opens «Bytt til innlogging med
   e-post» (`https://myntos.no/bytt-til-epost/`).
7. Type a password in «Nytt passord» (the hint gives the minimum length) and
   press «Velg passord og bytt».
8. «Byttet er gjort. Kontoen logger inn med e-postadressen din og passordet
   du nettopp valgte, og ikke lenger med BankID.» Press «Logg inn» and sign in
   with the address and the new password.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Denne lenken virker ikke lenger. … Kontoen logger fortsatt inn med BankID, og du kan be om en ny lenke under «Din konto».», with «Til Din konto» | The link was used, is older than 30 minutes, or a newer one was sent. | Sign in with BankID and ask for a new link. |
| «passordet må være minst 10 tegn» | Too short. The link is not used up. | Choose a longer password. |
| «Vi fikk ikke sendt e-posten. Prøv igjen om litt.» | The mail did not go out. | Try again later. |
| «du har bedt om for mange e-poster den siste timen, så vent litt og prøv igjen» | Too many links in an hour. | Wait, then use the newest link. |
| No «Bytt til innlogging med e-post» button | The account does not sign in with BankID, or this Myntos sends no e-mail. | Nothing to switch, or see Before they start. |

## Good to know

- **Everyone is signed out**, this browser included, when the password is
  chosen. The person signs in again with e-mail and password.
- **The address counts as confirmed**, since the link proved the inbox is
  theirs.
- **Google or Microsoft** can be linked once the switch is done:
  [Link Google or Microsoft](https://myntos.no/llms/help/your-account/link-google-or-microsoft.md).
- **Keys for AI agents** can be made again after the switch:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
- **Did not ask for it?** If such an e-mail arrives unasked, they should not
  open the link, and should sign the other devices out:
  [Sign out other devices](https://myntos.no/llms/help/your-account/sign-out-other-devices.md).
- The name on the account stays as it was.

## Related

- [Switch your account to BankID](https://myntos.no/llms/help/your-account/switch-to-bankid.md)
- [Sign in](https://myntos.no/llms/help/getting-started/sign-in.md)
