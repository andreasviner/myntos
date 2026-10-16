---
title: Understand the figures beside the register
description: What the «Oversikt» card beside the form on the Betaling page in Myntos shows: «Kasse og bank», «Inn», «Ut», «Netto», the estimated «Mva», the count of entries, the line about payments missing a bilag, and «Hvorfor kan ingenting slettes?». Use when someone asks what these numbers mean, why the VAT figure differs from the VAT return, or what «anslått å betale» means.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Understand the figures beside the register

Next to the form on the «Betaling» page, the card «Oversikt» shows the figures
a payment is about to change: what the bank account and cash box hold, the
year's money in and out, and the VAT building up. They are worked out from the
payments registered, and they are a summary for keeping an eye on things, not
a VAT return or finished accounts.

## They might ask

- «Hva betyr «Kasse og bank» i Myntos?»
- «Hvorfor stemmer ikke mva-tallet med mva-meldingen?»
- «Hva betyr «anslått å betale»?»
- "What do the numbers next to the payment form mean?"

## Before they start

Anyone with access to the company sees the card, whatever their role.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`). The
   card «Oversikt» is to the right of the form, or below it on a phone.
2. Read the figures:
   - «… føringer i 2026»: how many entries this calendar year.
   - «Kasse og bank»: what the bank accounts and cash box hold according to
     the books, across all years and including the opening balance: 1920,
     1900 and any account marked as a bank account on «Kontoplan». It should
     match the bank if everything is registered.
   - «Inn» and «Ut»: money in and out this year.
   - «Netto»: «Inn» minus «Ut».
   - «Mva (anslått å betale)» or «Mva (anslått til gode)»: the VAT owed now,
     not the year's: the periods not yet reported, plus those reported but
     not yet paid. «Å betale» means owed to Skatteetaten, «til gode» means
     Skatteetaten owes the company.
3. A yellow line such as «2 føringer mangler bilag. Legg ved PDF-en i
   registeret under.» counts payments with no receipt attached. See
   [Attach a receipt to a payment already registered](https://myntos.no/llms/help/payments/attach-a-document-to-a-payment.md).
4. «Hvorfor kan ingenting slettes?» opens a short explanation of why a payment
   cannot be deleted and what «Rediger» and «Reverser» do.

## Good to know

- The VAT figure is read off the books and is what is owed today, across all
  years. A period that has been reported counts until its payment is
  registered, and then drops out. The VAT return (mva-melding) is worked out
  per period on its own page, «Mva-melding», reached from the overview:
  «Mva-meldingen →».
- «Inn», «Ut» and «Kasse og bank» show nothing for books brought over from
  another program that kept the bank on another account than 1920, until
  that account is marked:
  [Mark an account as a bank account](https://myntos.no/llms/help/chart-of-accounts/mark-an-account-as-a-bank-account.md).
- A reversed payment and its reversal are left out of «Inn» and «Ut», and a
  corrected payment counts once.
- The same figures for the year are on the overview page, «Oversikt» in the
  top menu.

## Related

- [Read the overview](https://myntos.no/llms/help/overview/read-the-overview.md)
- [Read the payment register](https://myntos.no/llms/help/payments/read-the-payment-register.md)
- An agent with a key can read the same figures through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
