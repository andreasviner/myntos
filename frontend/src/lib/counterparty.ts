/**
 * What the motpart field means by the text in it.
 *
 * The field is one box: type a name, or pick one of the parties the company
 * already has. It used to be a list with a separate text box under it for a
 * name not yet registered, which put the same name on screen twice and made
 * the ordinary correction — the bilag named the wrong party, the right one is
 * printed further down the page — a matter of finding the second box.
 *
 * One box means the text has to be read back into a choice, and that reading
 * is a rule, so it lives here where it can be asserted. A name that is
 * registered is that party, whether it was picked from the list or typed out;
 * anything else is a new party, registered under exactly that name when the
 * entry is posted. The failure being guarded against is a typed "Telenor"
 * creating a second Telenor next to the one the register already has.
 */

import type { Party, PartyType } from './api';

/** A name reduced to what is worth comparing: case, and runs of whitespace. */
function normalised(name: string): string {
  return name.trim().replace(/\s+/g, ' ').toLocaleLowerCase('nb');
}

/**
 * The registered party ``text`` names, or ``null`` if it names none.
 *
 * Exact after normalising, never a prefix: "Telenor" must not quietly become
 * "Telenor Norge AS", because the user typing a name is the user saying who it
 * is. When two parties share the name — a customer and a supplier both called
 * Telenor is ordinary — the one on the side the money is going wins, which is
 * the same side the list puts first.
 */
export function resolveParty(text: string, parties: Party[], prefer: PartyType): Party | null {
  const wanted = normalised(text);
  if (wanted === '') return null;
  const named = parties.filter((party) => normalised(party.name) === wanted);
  return named.find((party) => party.party_type === prefer) ?? named[0] ?? null;
}

/**
 * The parties worth listing under what has been typed so far.
 *
 * Matched on the name and on the party's own number, anywhere in either, so
 * "10003" finds the party the register calls 10003. An empty query lists them
 * all, which is what the arrow beside the box is for.
 */
export function partiesMatching(query: string, parties: Party[]): Party[] {
  const wanted = normalised(query);
  if (wanted === '') return parties;
  return parties.filter(
    (party) => normalised(party.name).includes(wanted) || party.party_id.includes(wanted),
  );
}
