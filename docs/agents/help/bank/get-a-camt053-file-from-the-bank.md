---
title: Get a camt.053 file from the bank
description: What file a person must download from their online bank (nettbank) to import a bank statement into Myntos - the ISO 20022 account statement called camt.053 - and how to recognise it. Use when someone asks which file Myntos wants, or says their bank only gives PDF or Excel.
pages: /bank/
skill: myntos-bank
---

# Get a camt.053 file from the bank

Myntos reads the bank statement as a file in a standard format, ISO 20022
camt.053 ("BankToCustomerStatement"), which is an XML file. Norwegian banks
for businesses, among them DNB, SpareBank 1, Nordea and Handelsbanken, can
export it from the online bank. A PDF statement, a CSV or an Excel list is a
picture of the same information and cannot be imported.

## They might ask

- «Hvilken fil skal jeg laste ned fra banken til Myntos?»
- «Hva er camt.053?»
- «Banken gir meg bare PDF, hva gjør jeg?»
- "Which file do I download from my bank for Myntos?"

## Before they start

- Access to the company's online bank (nettbank for bedrift).

## Steps

1. In the online bank, find where the account's statement or transactions
   can be downloaded or exported. Banks name this differently; look for the
   account statement (kontoutskrift) or an export of transactions.
2. Choose the account Myntos should reconcile, and the period. A whole month
   is a good unit. Overlapping an earlier download is fine.
3. As the format, choose «camt.053» or «ISO 20022 XML». Not camt.052 (a report
   during the day, whose entries can still change) and not camt.054 (notices
   of single payments, without balances).
4. Save the file. It usually ends in `.xml`.
5. In Myntos, import it on «Bankavstemming»:
   [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The online bank offers only PDF, CSV or Excel | Some banks have to switch the file format on for the agreement, or keep it under another menu | Ask the bank how to download the account statement as ISO 20022 camt.053 |
| Myntos says «filen er ikke en ISO 20022-kontoutskrift (camt.053) …» | Another format was chosen | Download again with camt.053 or ISO 20022 XML |
| Myntos says «… går ikke opp …» or «… Filen er trolig ufullstendig» | The file was cut short or opened and saved in another program | Download again and import the file untouched; do not open and save it in Excel first |

## Good to know

- Only Norwegian accounts in Norwegian kroner can be imported for now.
- The file must be for an account registered on the company in Myntos.
- Myntos does not fetch statements from the bank by itself yet; the file is
  downloaded and uploaded by hand.

## Related

- [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md)
- An agent with a key can import the file through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
