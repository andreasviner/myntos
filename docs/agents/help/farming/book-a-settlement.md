---
title: Book a settlement from the dairy or slaughterhouse
description: How a farmer registers a settlement (melkeoppgjør from Tine, slakteoppgjør from Nortura, kornoppgjør, tømmeroppgjør) on the Betaling page in Myntos, one line per post - the sale, omsetningsavgift, andelsinnskudd, kraftfôr set off - so the sale, the VAT and the deductions each land right. Use when someone asks how to book a settlement, an oppgjør or an avregning from a cooperative.
pages: /kontantstrom/
skill: myntos-landbruk
---

# Book a settlement from the dairy or slaughterhouse

A settlement (oppgjør or avregning) from Tine, Nortura, Felleskjøpet, the mill
or the timber buyer is the farm's own **sales document**: the buyer writes it
for the farmer (bokføringsforskriften § 5-2-1). So it is money in, even with
the buyer's name at the top. What the buyer pays is the sale less what it
deducted: omsetningsavgift, the share in the cooperative (andelsinnskudd), feed
it sold the farm. Each of those is booked as a line of its own, so the sale, the
VAT and every deduction land where they belong, and the VAT in the books is the
VAT printed on the settlement.

## They might ask

- «Hvordan fører jeg melkeoppgjøret fra Tine?»
- «Slakteoppgjøret har trekk for omsetningsavgift og andel, hvordan fører jeg det?»
- «Tine trakk kraftfôret i oppgjøret»
- «Hvilken mva er det på melk til meieriet?»

## Before they start

- The farming add-on must be on: [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md).
- They need the role «Medlem», «Administrator» or «Eier».
- The settlement PDF, and the amount that arrived in the bank.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Upload the settlement. Myntos reads it as money in («Penger inn») and says
   «Dette ser ut som et oppgjør kjøperen har skrevet ut for deg. Det er
   salgsdokumentet ditt, ikke en regning.»
3. Check «Hva gjaldt det?»: «Melkeoppgjør», «Slakteoppgjør», «Kornoppgjør og
   salg av frø» or «Tømmeroppgjør og salg av ved».
4. Click «Del opp i flere linjer». The first line is the sale; set its «Beløp
   inkl. mva» to the sale with VAT as the settlement shows it, not the amount
   paid out. On a melkeoppgjør the posts down to «Netto avregnet» (milk price,
   supplements, omsetningsavgift) are the sale, and the VAT is calculated on
   that net: the line is the net plus 25 %, which is the settlement's first
   «Beløp inkl. mva.» once the pristilskudd is taken out as its own line.
5. Add a line for each deduction with «Legg til linje»:
   - «Trekk: omsetningsavgift og forskningsavgift», only if the sale line was
     entered before the levy. It has 25 % VAT, and its «Beløp inkl. mva» is the
     levy plus 25 %. On a slaughter or grain settlement, choose 3041 or 3001
     under «Konto» on that line, so it reduces the right sale.
   - «Trekk: andelsinnskudd i samvirke» for the share kept back.
   - «Kraftfôr» for feed set off, with its own 25 % VAT.
   - «Husdyrkontroll» if it is deducted.
   - «Distrikts- og grunntilskudd i melkeoppgjøret» or «Distriktstilskudd i
     slakteoppgjøret» as a line of money in, when the settlement pays it.
   - On a timber settlement, «Trekk til skogfond» for what went to the
     skogfondskonto.
6. Check that «Til sammen … inn på kontoen» matches the bank.
7. Press «Registrer betaling».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No farm types in «Hva gjaldt det?» | The farming add-on is off | [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md) |
| The settlement came in as «Penger ut» | The reader did not recognise it | Click «Penger inn»; a settlement is always the farm's sale |
| «Linjene går i null. …» | The deductions equal the sale | Check the amounts against the settlement |

## Good to know

- Milk delivered to the dairy, slaughter animals, live animals and grain are
  sold at **25 %** VAT, not 15 %: they are not food until processed. The types
  propose 25 %; keep it unless the settlement says otherwise.
- The omsetningsavgift is the farmer's own levy that the buyer collects, and
  the dairy and the slaughterhouse calculate the VAT on the price after it.
  That is why «Trekk: omsetningsavgift og forskningsavgift» has 25 %: the VAT
  in the books must be the VAT on the settlement, which is the farm's sales
  document. If a settlement shows VAT on the price before the levy, set the
  line's mva to 0.
- Posts printed below the VAT line, such as «Medlemsavg HK» for
  husdyrkontrollen, have their own VAT and are their own lines
  («Husdyrkontroll»).
- A slaughter settlement where every post has the same 25 % (Fatland's
  «Avregning»: the animals, «kilo pluss», the slaughterhouse's own
  supplements, «Kvalitetstilskudd storfe» and «Omsetningsavgift» summed to
  «Sum brutto», with «Moms» on that sum) can be one line: «Slakteoppgjør»,
  with the amount paid out. The VAT then comes out exactly as the settlement
  prints it, and there is no need to click «Del opp i flere linjer».
- «Kvalitetstilskudd storfe» is paid by the state through the slaughterhouse
  and has 25 % VAT on the settlement; keep it in the «Slakteoppgjør» line. It
  is not a distriktstilskudd.
- The share in the cooperative is not a cost: it stays the farmer's money, on
  account 1355, and is paid back when the farm stops delivering.
- Tine's etterbetaling is booked as «Melkeoppgjør», in the year the annual
  meeting decides it.

## Related

- [Split a payment into several lines](https://myntos.no/llms/help/payments/split-a-payment-into-lines.md)
- [Choose the VAT for farm sales](https://myntos.no/llms/help/farming/choose-the-vat-for-farm-sales.md)
- [Book production subsidies](https://myntos.no/llms/help/farming/book-production-subsidies.md)
- An agent with a key can do this through the API:
  [myntos-landbruk](https://myntos.no/llms/skills/myntos-landbruk.md)
