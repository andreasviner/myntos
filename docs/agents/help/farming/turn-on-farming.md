---
title: Turn on farming
description: How a person switches on the farming add-on (landbruk) in Myntos on the Kontoplan page, what it adds (accounts from NIBIO's farm chart, farm types in the payment register, a bilag reader that knows a settlement from Tine is a sale), who can do it, and how to switch it off. Use when someone with a farm, gårdsbruk or forest asks whether Myntos supports landbruk, or how to get farm accounts.
pages: /kontoplan/
skill: myntos-landbruk
---

# Turn on farming

Myntos is an accounting program for every kind of business. A farm or a
forest owner gets an add-on on top, called «Landbruk»: the accounts from the
farm sector's standard chart of accounts (NIBIO's Landbrukets kodesystem, used
by every farm accountant), types in the payment register for settlements,
subsidies, feed and the deductions on a settlement, each with the right VAT,
and a bilag reader that knows a settlement from Tine or Nortura is a sale. A
company registered with næringskode 01 (jordbruk) or 02 (skogbruk) has it on
already; any other company can switch it on.

## They might ask

- «Kan jeg bruke Myntos til gårdsregnskapet?»
- «Har Myntos landbrukskontoplan?»
- «Hvordan slår jeg på landbruk i Myntos?»
- «Hvorfor ser jeg typer for melkeoppgjør?»
- "Does Myntos support farm accounting?"

## Before they start

- Anyone can see whether it is on. Only «Administrator» and «Eier» can switch
  it, because it adds accounts to the kontoplan:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).

## Steps

1. Click the company's name at the top right and choose «Innstillinger for
   selskapet». In the «Selskapet» section, follow the link «kontoplanen»
   (`https://myntos.no/kontoplan/`).
2. Scroll to the card «Landbruk» at the bottom. It says whether the add-on is
   on and why, for example «Slått på, fordi selskapet er registrert med
   næringskode 01.410, som er jord- eller skogbruk.»
3. Click «Slå på landbruk». If the add-on is on already but its accounts are
   not all in the kontoplan yet, the button says «Legg til landbrukskontoene».
4. The card says what happened: «Landbruk er slått på. 69 kontoer er lagt til
   i kontoplanen.» The new accounts appear in the list above, each placed in its
   post in the næringsspesifikasjon.
5. The farm types are now in «Hva gjaldt det?» on «Betaling».

To switch it off, click «Slå av landbruk» on the same card. «La næringskoden
avgjøre» hands the decision back to the company's næringskode.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No buttons on the card | Their role is «Medlem» or «Leser» | Ask an «Administrator» or the «Eier» |
| «Konto 4020 fantes fra før med et annet navn og er ikke endret …» | The company already used that number for something of its own | Nothing is wrong; the farm type for it books to the company's own account. Rename it in the list if it should be the farm account |
| «Av. Selskapet er ikke registrert med næringskode for jordbruk (01) eller skogbruk (02).» | The register does not list the company as a farm | Click «Slå på landbruk» if it farms anyway |

## Good to know

- Switching off removes nothing. The accounts stay (deactivate them in the
  list if they are in the way), and payments already registered with a farm
  type keep it.
- Milk delivered to the dairy, slaughter animals, live animals and grain are
  sold at 25 % VAT, not 15 %, because they are not food until processed. The
  farm types use the right rate:
  [Choose the VAT for farm sales](https://myntos.no/llms/help/farming/choose-the-vat-for-farm-sales.md).
- What is not in Myntos yet: settlements fetched automatically from
  Landbrukets Dataflyt, an automatic split of VAT on costs shared by the farm
  and another business (two VAT returns themselves are built:
  [File two VAT returns](https://myntos.no/llms/help/vat/file-two-vat-returns.md)),
  livestock counts, jordbrukskonto and the tax return. Each
  settlement is registered from its PDF.

## Related

- [Book a settlement from the dairy or slaughterhouse](https://myntos.no/llms/help/farming/book-a-settlement.md)
- [Book production subsidies](https://myntos.no/llms/help/farming/book-production-subsidies.md)
- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- An agent with a key can do this through the API:
  [myntos-landbruk](https://myntos.no/llms/skills/myntos-landbruk.md)
