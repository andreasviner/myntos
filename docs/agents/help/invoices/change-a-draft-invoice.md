---
title: Change a draft invoice
description: How a person opens a saved draft invoice again and changes it on the Faktura page in Myntos, with «Endre» in the invoice list or «Tilbake og endre» in the review. Use when someone asks how to edit an invoice they have not issued yet, or where their unfinished invoice went.
pages: /faktura/
skill: myntos-faktura
---

# Change a draft invoice

An invoice is saved as a draft the moment the person presses «Se over
fakturaen». A draft has no number and is not in the books, so it can be
changed as often as needed. It stays in the list on the right, marked
«Utkast», until it is issued or deleted. Once issued, it can no longer be
changed.

## They might ask

- «Hvordan endrer jeg en faktura jeg ikke har sendt?»
- «Hvor ble det av fakturaen jeg holdt på med?»
- «Kan jeg fortsette på et utkast?»
- "How do I edit a draft invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. In the list on the right, find the row with «utkast» under «Nr.» and
   «Utkast» under «Status», and press «Endre». The form opens as «Endre
   utkast» with everything filled in. (Straight after the review, «Tilbake og
   endre» does the same.)
3. Change what is needed.
4. Press «Se over fakturaen» to save the changes and see the review again.
   Then issue it, or leave it as a draft for later.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Endre» on the row | The invoice is issued («Utstedt»), or their role is «Leser» | An issued invoice is corrected with a credit note: [Correct an issued invoice](https://myntos.no/llms/help/invoices/correct-an-issued-invoice.md) |

## Good to know

- The draft picks up the company's details as they are when it is saved, and
  issuing reads them once more, so an account number added since is used.
- A registered customer's details are copied onto the draft when it is saved
  with that customer chosen.

## Related

- [Delete a draft invoice](https://myntos.no/llms/help/invoices/delete-a-draft-invoice.md)
- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can change drafts through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
