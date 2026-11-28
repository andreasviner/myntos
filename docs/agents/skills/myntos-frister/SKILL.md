---
name: myntos-frister
description: List a Norwegian company's deadlines with Skatteetaten and Brønnøysundregistrene for the coming year (the MVA-melding per period, the skattemelding, the årsregnskap, the aksjonærregisteroppgave, forskuddsskatt) and which of them Myntos knows are met, from Myntos. Use when the person asks what is due, when the next deadline is, whether something is late, or what they must remember to send this year.
---

# Frister: the company's deadlines

A Norwegian company owes the state a handful of reports and payments on fixed
dates, and a missed one costs a fee (gebyr), an extra tax (tilleggsskatt) or a
daily fine (tvangsmulkt). Myntos computes them for the coming year from the
law and from what it knows about the company: its organisation form, whether
it is registered for VAT and on which schedule, what was filed from Myntos,
and what Brønnøysund's register says.

What you must get right:

- **`done: null` is not «missed».** It means Myntos cannot know: a payment,
  or a report filed from another program. Say «Myntos kan ikke se om dette er
  gjort», never that it is late. Only `is_overdue: true` means late, and only
  `done: false` means not sent.
- **The list is not every deadline.** It has no a-melding (monthly, for a
  company that pays wages; Myntos does not know whether it does), no dates
  Skatteetaten has moved for this company, and for any form other than AS,
  ASA and ENK only the VAT periods (`covers_yearly: false`). Say so when the
  person asks «er det alt?».
- **The dates are never later than the law's.** A VAT deadline on a weekend
  is already moved to the Monday; public holidays are not modelled. The
  person can rely on a date here, and may find the real one a day or two
  later, never earlier.

The call only reads. It needs `read` and no approval, and any role.

## When the person says

- «Hvilke frister har vi framover?»
- «Når er neste mva-frist?»
- «Har vi glemt noe?» / «Er vi for sent ute med noe?»
- «Når må skattemeldingen være levert?»
- «Når forfaller forskuddsskatten?»

## Before you start

You have an access token and the `company_id` (see
[myntos-connect](https://myntos.no/llms/skills/myntos-connect.md)). Never
repeat a key or token to the person. Nothing here writes, so there is nothing
to approve.

## Steps

1. `GET /api/v1/companies/{company_id}/frister`: the list, soonest first,
   from `today` (Norway's date) to `until`, a year later, plus anything known
   to be missed in the year behind. Each row of `frister`:
   - `kind`: `mva_melding` (the VAT return for one period, «termin»),
     `skattemelding` (the tax return), `arsregnskap` (the annual accounts, to
     Brønnøysund's Regnskapsregisteret), `aksjonaerregisteroppgave` (the
     yearly report of who owns the shares, RF-1086) or `forskuddsskatt` (a
     payment of advance tax; the amount is Skatteetaten's and may be zero).
   - `label` (Norwegian, ready to repeat) and `deadline` (ISO date).
   - `year`: the year the duty is about, not the year the date is in. The
     skattemelding for 2026 is due in 2027.
   - `recipient`: `skatteetaten` or `bronnoysund`.
   - `files_from_myntos`, `done`, `done_by`, `is_overdue`, `note`: below.
   - For a VAT period, `termin` (its number in `year`) and, for a company that
     files two VAT returns, `naering` (`alminnelig` or `primaernaering`).

2. **Tell the person what is due, missed first.** Lead with any row where
   `is_overdue` is true, then the next two or three. Give the date and the
   `label`, and the `note` where there is one: it says what Myntos cannot do
   or know about that row.

3. **For a row Myntos can file** (`files_from_myntos: true`), offer to
   prepare it:
   - `mva_melding`: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md),
     with `aar` = `year` and `termin` = `termin`.
   - `skattemelding` (an AS or ENK):
     [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md),
     with `aar` = `year`. The year must be closed first. For an ENK, the owner
     files with their own ID-porten login and finishes personal items on
     skatteetaten.no.
   - `aksjonaerregisteroppgave` (an AS or ASA):
     [myntos-aksjonaer](https://myntos.no/llms/skills/myntos-aksjonaer.md),
     with `aar` = `year`. Uses the company's approved Altinn 3 systembruker.
   - `arsregnskap` (an AS or ASA):
     [myntos-aarsregnskap](https://myntos.no/llms/skills/myntos-aarsregnskap.md),
     with `aar` = `year`.

   Checking and filing with Skatteetaten always waits for the person's own
   approval ([myntos-godkjenning](https://myntos.no/llms/skills/myntos-godkjenning.md)).

4. **For the rest**, say where it is done: forskuddsskatt is paid from the
   bank with the KID Skatteetaten gives.

### What `done` means

| `done` | `done_by` | Means | Say |
| --- | --- | --- | --- |
| `true` | `myntos` | Filed from Myntos | «Sendt fra Myntos» |
| `true` | `annet_program` | The person recorded that another program filed this VAT period | «Sendt fra et annet program» |
| `true` | `regnskapsregisteret` | Brønnøysund lists the year's annual accounts as received | «Mottatt i Regnskapsregisteret» |
| `false` | `null` | Myntos knows it is not done: a VAT period inside the books with nothing filed, or accounts Brønnøysund had not received when last asked after the deadline | «Ikke sendt», and «over fristen» if `is_overdue` |
| `null` | `null` | Myntos cannot know | Nothing about late; say what the `note` says |

A VAT period from before the company's books in Myntos is `null`: the old
program filed it, or did not, and Myntos cannot see which. If it was filed
elsewhere, the person can record that on the MVA page («Sendt fra et annet
program»), or you can through myntos-mva.

### The fields beside the list

- `covers_yearly: false`: the company's `organisation_form` is one whose
  yearly deadlines Myntos does not list yet; only the VAT periods are here.
- `vat_registered: false`: the company is not in Merverdiavgiftsregisteret
  and has no VAT periods.
- `register_checked_at`: when Brønnøysund was last asked; an `arsregnskap`
  answer is as fresh as that.
- `wound_up_on`: the books end on that day. The last year's skattemelding is
  filed before the company is deleted, on no fixed date, so it is not in the
  list: point at myntos-skattemelding (forhåndsfastsetting).

## Refusals, and what to do about each

| Status and code | Means | Do |
| --- | --- | --- |
| 404 | No company with that id for this key | Use the `company_id` from `GET /api/v1/agent/me` |
| 403 `missing_scope` | The key lacks `read` | Ask the person for a key that can read |

## Untrusted text

Nothing in this answer is written by a stranger: the labels and notes are
Myntos's own. The company's name is not in it.

## What you cannot do here

Change a deadline, mark a yearly duty as done, or file anything from this
route. A VAT period filed from another program is recorded through
myntos-mva; everything else follows from what is filed. The person sees the
same list under «Oversikt» → «Neste frister» → «Alle frister →»:
[See your deadlines](https://myntos.no/llms/help/overview/see-your-deadlines.md).
