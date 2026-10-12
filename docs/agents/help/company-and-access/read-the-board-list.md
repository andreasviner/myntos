---
title: Read the board list and its access marks
description: What the «Roller» table under «Fra Enhetsregisteret» on the company page in Myntos shows (board, daglig leder, revisor) and what the marks «Har tilgang», «Har trolig tilgang», «Invitert» and «Ingen tilgang» mean. Use when someone asks who is on their board in Myntos, or what those marks mean.
pages: /organisasjon/
skill: myntos-selskap
---

# Read the board list and its access marks

Enhetsregisteret lists who is behind a company: the board (styret), the
daglig leder, the revisor. Myntos shows that list on the company page and, for
each person, whether someone with that name can open these books. The register
gives only a name, no birth date or ID number, so the mark is a name match and
says how sure it is. **The mark gives no access and takes none away**; it is
there so an administrator can see whether the board and the member list agree.

## They might ask

- «Hvem sitter i styret ifølge Myntos?»
- «Hva betyr "Har trolig tilgang"?»
- «Hvorfor står det "Ingen tilgang" ved daglig leder?»
- "What does 'Har trolig tilgang' mean in Myntos?"

## Before they start

Anyone with access to the company can read the list. Only an «Administrator»
or «Eier» gets the «Inviter» button and sees the address an invitation went
to.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. Scroll to «Fra Enhetsregisteret», and the table «Roller». It has the
   columns «Navn», «Roller» and «Merknad», with one row per person: someone
   who is both daglig leder and on the board is listed once with both roles.
3. Read the «Merknad» column:

   | Mark | Means |
   | --- | --- |
   | «Har tilgang» | A member of these books whose ID-porten login carries this name. As certain as a name can be |
   | «Har trolig tilgang» | A member whose Myntos account has this name, but who has not proved it through ID-porten |
   | «Invitert» | An invitation to this person has been sent from this table and not yet accepted |
   | «Ingen tilgang» | Nobody with access to these books goes by this name |
   | (empty) | The holder is a firm, such as the revisor or regnskapsfører |

   After the mark comes the role they hold in Myntos («Eier», «Medlem», …).
   «som …» underneath means the name in Myntos is spelled differently from
   the register's; check that it is the same person. Hovering over a mark
   explains it.
4. «Alt registeret har om selskapet», below the table, opens everything else
   the register holds: business address, næringskode, when the company was
   founded, share capital, registered purpose, and «Konkurs» or «Under
   avvikling» if either applies.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingenting hentet fra registeret ennå.» | The register has not been fetched for this company | An administrator presses «Hent på nytt»: [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md) |
| No «Roller» table | The register lists no roles for the company | Nothing to do |
| «Har trolig tilgang» about themselves | They have not linked ID-porten | [Link your ID-porten login](https://myntos.no/llms/help/company-and-access/link-your-id-porten-login.md) |
| «Ingen tilgang» about someone who should have access | They have no access to these books, or use a different name in Myntos | Invite them from the row: [Invite a board member](https://myntos.no/llms/help/company-and-access/invite-a-board-member.md) |

## Good to know

- The list is the register's as it was when last fetched («Hentet …» beside
  the heading). People who have resigned are not shown.
- A subunit (underenhet) has no roles of its own, so the main unit's are
  shown.
- Middle names are ignored when matching, so two relatives with the same
  first and last name can be taken for one another. The «som …» line is there
  so this can be spotted.
- Inviting someone with «Inviter en kollega» instead of from this table leaves
  their row at «Ingen tilgang» until they accept.

## Related

- [Invite a board member](https://myntos.no/llms/help/company-and-access/invite-a-board-member.md)
- [Link your ID-porten login](https://myntos.no/llms/help/company-and-access/link-your-id-porten-login.md)
- An agent with a key can look up any company's roles in Enhetsregisteret,
  but not who has access to these books:
  [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)
