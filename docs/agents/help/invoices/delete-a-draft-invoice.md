---
title: Delete a draft invoice
description: How a person deletes a draft invoice that was never issued, with «Slett» in the invoice list on the Faktura page in Myntos, and why an issued invoice cannot be deleted. Use when someone asks how to delete or remove an invoice.
pages: /faktura/
skill: myntos-faktura
---

# Delete a draft invoice

A draft that was never issued can be deleted. It has no number, so deleting
it leaves no gap in the invoice series. An issued invoice can never be
deleted, by anyone: the law requires it to be kept, and it is corrected with a
credit note instead.

## They might ask

- «Hvordan sletter jeg en faktura?»
- «Jeg vil fjerne et utkast»
- «Kan jeg slette en faktura jeg har sendt?»
- "How do I delete an invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. In the list on the right, find the draft (status «Utkast») and press
   «Slett».
3. Confirm «Slette utkastet til …?» with OK. The draft is gone; this cannot be
   undone.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Slett» on the row | The invoice is issued, or their role is «Leser» | An issued invoice is reversed with a credit note: [Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md) |
| «Sletting feilet.» or another message at the top | The draft could not be deleted, for instance because it was issued in another window | Reload the page and look at its status |

## Good to know

- Deleting a draft never touches the books: a draft was never posted.

## Related

- [Change a draft invoice](https://myntos.no/llms/help/invoices/change-a-draft-invoice.md)
- [Correct an issued invoice](https://myntos.no/llms/help/invoices/correct-an-issued-invoice.md)
- An agent with a key can delete drafts through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
