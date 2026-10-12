---
title: Import a bank statement
description: How a person uploads the bank statement file (kontoutskrift, camt.053) from their online bank to the Bankavstemming page in Myntos, and what each refusal means. Use when someone asks how to get their bank transactions into Myntos or why a statement file was rejected.
pages: /bank/
skill: myntos-bank
---

# Import a bank statement

Myntos compares what the bank says moved on the company's account with what
has been entered in the books. For that it needs the bank's statement as a
file: an ISO 20022 statement, called camt.053, downloaded from the online
bank. The import reads the file, checks that it adds up, keeps it as
documentation, and lists every movement on the page. Nothing already entered
is changed. The file cannot be deleted afterwards: it is kept as part of the
books' documentation, like a receipt.

## They might ask

- «Hvordan får jeg banktransaksjonene inn i Myntos?»
- «Hvordan laster jeg opp kontoutskriften?»
- «Myntos avviser filen fra banken»
- "How do I import my bank statement into Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier» in this company. A «Leser»
  does not see the import form.
- The bank account must already be registered on the company, under
  «Selskapet» → «Kontonummer»:
  [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md).
- The statement file, in camt.053 format:
  [Get a camt.053 file from the bank](https://myntos.no/llms/help/bank/get-a-camt053-file-from-the-bank.md).

## Steps

1. Open «Oversikt» in the top menu, and under the year's figures follow the
   link «Bankavstemming» (`https://myntos.no/bank/`). The company's name is
   the page heading; check it is the right company.
2. In the box «Importer kontoutskrift», at «Fil fra nettbanken (camt.053)»,
   choose the file downloaded from the online bank.
3. Press «Importer».
4. The page answers «Importert: 12 nye bevegelser.» (with «…, 3 fantes fra
   før.» when some of them were already imported). The movements appear
   under «Bevegelser på kontoen», and the statement under «Importerte
   kontoutskrifter».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Importer kontoutskrift» box | Their role is «Leser» | Ask a «Medlem», «Administrator» or «Eier» to import it |
| «Velg filen du lastet ned fra nettbanken.» | No file was chosen | Choose the file, then «Importer» |
| «kontoutskriften gjelder …, som ikke er registrert på dette selskapet …» and the link «Legg til kontoen under Selskapet» | The account in the file is not on the company's list, or it is another company's statement | Check the company in the heading. If it is right, add the account (an «Administrator» or «Eier» can) and import again |
| «filen er ikke en ISO 20022-kontoutskrift (camt.053). Velg «camt.053» eller «ISO 20022 XML» når du laster ned fra nettbanken» | The file is a PDF, CSV, Excel or another XML file | Download the statement again in camt.053 format |
| «filen er camt.052, ikke camt.053: …» (or camt.054) | An intraday report or a payment notification, not the statement | Download the account statement (camt.053) instead |
| «Kontoutskrift 1 går ikke opp: …» or «… Filen er trolig ufullstendig» | The file does not add up against its own balances: it is cut short or edited | Download the whole period again, untouched |
| «… er i EUR. Myntos importerer bare kontoutskrifter i norske kroner ennå …» | A foreign-currency account or entry | Not possible yet; enter those payments under «Betaling» |
| «bankreferansen … er allerede importert med …, men denne filen sier …» | A movement already imported now has another amount or date: the bank changed it, or two files are mixed up | Nothing was imported. Ask the bank whether the statement was corrected |
| «filen er større enn 25 MB» | Too large | Download a shorter period |

## Good to know

- Importing the same file twice does nothing: «Denne filen er importert fra
  før. Ingenting nytt ble lagret.» A statement that overlaps one already
  imported adds only the new movements, so it is safe to import a month
  that partly repeats the last one.
- «… som banken ikke har bokført ennå, ble utelatt.» means some movements
  were still pending at the bank. They come with a later statement.
- Only booked movements, Norwegian accounts and Norwegian kroner are imported.
- If an AI with a key imports for the person, the import may wait for their
  approval under «Godkjenning» first:
  [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md).

## Related

- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md)
- [Download an imported bank statement](https://myntos.no/llms/help/bank/download-an-imported-bank-statement.md)
- An agent with a key can do this through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
