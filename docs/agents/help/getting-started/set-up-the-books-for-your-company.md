---
title: Set up the books for your company
description: How a person sets up a company's books in Myntos on the Sett opp regnskapet page, by picking the company from Altinn or typing its organisasjonsnummer, choosing the first financial year and adding a bank account number. Use when someone asks how to start using Myntos for their company, how to add a company, or what the first financial year means.
pages: /nytt-selskap/
---

# Set up the books for your company

The company already exists in Enhetsregisteret; what Myntos sets up is its
books: a chart of accounts, VAT codes and the first financial year, ready to
post in at once. The page asks three things in three steps: which company,
from which year, and which bank account invoices are paid to. The person who
sets the books up becomes their owner («Eier»). An accountant sets up each
client the same way, and each company's books are separate.

## They might ask

- «Hvordan kommer jeg i gang med Myntos for firmaet mitt?»
- «Hvordan legger jeg til et selskap?»
- «Hva betyr "Første regnskapsår i Myntos"?»
- "How do I add my company to Myntos?"

## Before they start

- They need a Myntos account and must be signed in:
  [Create a Myntos account](https://myntos.no/llms/help/getting-started/create-an-account.md).
- The company's organisasjonsnummer, or a BankID (or Buypass or Commfides)
  to fetch their companies from Altinn instead.
- The company's bank account number, if it will send invoices (it can be
  added later).
- If the company kept books in another program until now, read
  [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
  first. The choice of first year depends on it.

## Steps

1. Open the page:
   - with no books yet, the front page says «Ingen regnskap ennå»: press
     «Legg til selskapet ditt»;
   - otherwise, open the company menu at the top right (the button with the
     company's name) and press «Legg til selskap» at the bottom;
   - or go to `https://myntos.no/nytt-selskap/`.

   The page is headed «Sett opp regnskapet», with the steps «1 Selskapet»,
   «2 Regnskapet», «3 Ferdig».

2. **Step 1, «Selskapet».** Two ways; the first saves typing and confirms the
   company at the same time.
   - **From Altinn**, when the card «Selskapene dine i Altinn» is shown:
     press «Hent selskapene mine» and log in with ID-porten. Back on the page,
     the card lists the companies they have rights for in Altinn. Press one,
     and the page moves straight to step 2. A company marked «Allerede satt
     opp» is already in their Myntos and cannot be picked again; one marked
     «Registrert av en annen» has books set up by somebody else (see the
     table below).
   - **By number**, under «Skriv inn organisasjonsnummeret» (or «Eller skriv
     inn organisasjonsnummeret selv» when the Altinn list is shown): type the
     nine digits in «Organisasjonsnummer». «Navn på selskapet» fills in from
     Enhetsregisteret, and the line under the number says what the register
     knows, for example the company form, the town, and «ikke
     mva-registrert», «registrert konkurs» or «under avvikling» where that is
     true. Press «Neste».

3. **Step 2, «Regnskapet».** A card at the top shows the company they chose,
   with «Bytt selskap» to go back. Under «Litt om regnskapet»:
   - «Første regnskapsår i Myntos»: filled in with this year. The page warns:
     «Du kan ikke føre på datoer før dette året, og mva-satsene vi setter opp
     gjelder fra da.» Choose the first year whose entries will be made in
     Myntos.
   - «Kontonummer» (optional): the account customers pay invoices to, eleven
     digits. No invoice can be issued until one is there. Further accounts
     are added later on the company's settings page.

   Press «Sett opp regnskapet». («Tilbake» goes to step 1.)

4. **Step 3, «Ferdig».** «Regnskapet for … er satt opp», and «Kontoplanen,
   mva-kodene og regnskapsåret er på plass. Du kan begynne å føre med én
   gang.» If the company came from the Altinn list, the page also says
   «Eierskapet er bekreftet i Altinn.» What to do from here:
   [After setting up the books](https://myntos.no/llms/help/getting-started/what-to-do-after-setup.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Et organisasjonsnummer er ni siffer.» | Fewer or more than nine digits. | Check the number. Spaces are fine. |
| «organisasjonsnummeret har feil kontrollsiffer» | A digit is wrong; the number cannot exist. | Check it against brreg.no or a letter from the authorities. |
| A message ending «Du kan skrive navnet selv.» under the number | Enhetsregisteret did not answer or does not know the number. | Type the registered name in «Navn på selskapet» exactly as registered, and continue. |
| «organisasjonen fører allerede regnskap for …» | This account already has books for that number. | Pick it from the company menu at the top right instead. |
| «Selskapet har allerede et regnskap registrert hos noen andre. Be en administrator der om tilgang.», with «Opprett nytt regnskap for …» | Somebody else has set up and confirmed books for this company in Myntos. | Usually: ask whoever keeps those books to invite them. Pressing «Opprett nytt regnskap for …» makes a second, separate set of books, which can never be confirmed in Altinn while the other one holds the company. |
| «Innloggingen ga ikke høyt nok sikkerhetsnivå. Logg inn med BankID, Buypass eller Commfides i stedet for MinID.» | MinID is not enough for Altinn here. | Press «Hent selskapene mine» again and choose BankID, Buypass or Commfides. |
| «Innloggingen ble avbrutt.» / «Innloggingen tok for lang tid. Prøv en gang til.» | The ID-porten login was cancelled or took too long. | Try again, or type the number. |
| «Innloggingen kunne ikke starte. Det er en feil hos oss, ikke hos deg. Vi har fått beskjed.» | A fault on Myntos's side. | Type the number instead. |
| «Altinn har ingen selskaper registrert på deg. Du kan skrive inn nummeret selv.» | Altinn holds no rights for this person (common for someone who is not on the company's board or registered as its contact). | Type the number. |
| «Innloggingen i ID-porten har gått ut. Logg inn på nytt.» | The ID-porten login expired. | Press «Hent selskapene mine» again. |
| «kontonummeret må være elleve siffer. …» or «kontonummeret … har feil kontrollsiffer. Kontroller det mot nettbanken» | The bank account number is wrong. Foreign numbers and IBAN are not supported yet. | Correct it, or leave the field empty and add it later. |
| «Du kan ikke sette opp nye regnskap på denne kontoen. Er du invitert inn i et selskap, er det bare det selskapet du har tilgang til.» | The account may not add companies. | Ask whoever keeps the books to set them up, or sign up with an account of their own. |

## Good to know

- **The register decides some things, not the person.** Whether the company
  is VAT-registered, whether it is in Foretaksregisteret, its address and its
  company form are taken from Enhetsregisteret. They decide what every
  invoice may and must say, which is why they are not typed in.
- **A company set up by typing its number is «ubekreftet»** until somebody
  confirms in Altinn that they may act for it. The books work fully in the
  meantime; see
  [Confirm the company](https://myntos.no/llms/help/company-and-access/confirm-the-company.md).
- **The ID-porten login is not the Myntos login.** It asks Altinn which
  companies the person may act for, and nothing more here.
- **A company with history brings it from the old program's SAF-T files**:
  the opening balance, and the vouchers of every year uploaded, earlier years
  included. Years before «Første regnskapsår i Myntos» are added when the files
  reach further back:
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).
- **More companies later** are added the same way, with «Legg til selskap».

## Related

- [After setting up the books: what to do next](https://myntos.no/llms/help/getting-started/what-to-do-after-setup.md)
- [Move your books to Myntos from another program](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- [Switch between companies](https://myntos.no/llms/help/overview/switch-company.md)
- [Add another company](https://myntos.no/llms/help/company-and-access/add-another-company.md),
  including why «Legg til selskap» can be missing
