/**
 * The «Mva-kode» select, as /kontantstrom/ and /manuelt-bilag/ draw it.
 *
 * It was 27 options of «86: Tjenester kjøpt fra utlandet, høy sats (25 %),
 * med fradragsrett», with the rate last, so a select as wide as its column
 * showed «1: Inngående mva, høy sa…» and hid the one thing a person checks
 * it for. The rate goes first now, and the codes are grouped by the side of
 * a trade they belong to, which is what the backend's `kind` says.
 *
 * On /kontantstrom/ the category says which side the payment is, so the other
 * side's codes are hidden rather than removed: a value set from a proposal or
 * from an entry being corrected still lands even when it is on the wrong side,
 * and the person sees it instead of the select quietly falling back to the
 * first code. Safari has not always hidden an `<option>`; there the list is
 * the whole one, grouped.
 */

import type { Direction, TaxCode, TaxCodeKind } from './api';

/** The groups in the order the MVA-melding and Skatteetaten's portal show them. */
const GROUPS: readonly { kind: TaxCodeKind; label: string }[] = [
  { kind: 'utgaaende', label: 'Salg' },
  { kind: 'inngaaende', label: 'Kjøp' },
  { kind: 'omvendt', label: 'Import og omvendt avgiftsplikt' },
];

/** The rate as the descriptions write it: «25», «11,11», «0». No number is made of it. */
export function percent(value: string): string {
  const [whole = '', fraction = ''] = value.trim().split('.');
  const kept = fraction.replace(/0+$/, '');
  return kept === '' ? whole : `${whole},${kept}`;
}

/** «(25 %)» or «(11,11 %)» inside a description: the rate it was written with. */
const STATED_RATE = /\s*\(\d+(?:,\d+)?\s*%\)/;

/**
 * «1: 25 % – Inngående mva, høy sats». Code first, because that is what the
 * register and the MVA page call it; then the rate, read off `rate_percent`
 * rather than the text, since a manual voucher's rate is the one on its date.
 *
 * A zero rate is shown only where the description states one. Code 5 is
 * zero-rated and says «(0 %)»; codes 0 and 6 are outside VAT altogether, and
 * «0 %» in front of them would put them beside 5 as if they were the same.
 */
export function taxCodeLabel(code: TaxCode): string {
  const rest = code.description.replace(STATED_RATE, '').trim();
  const rate = percent(code.rate_percent);
  const showRate = rate !== '' && (rate !== '0' || STATED_RATE.test(code.description));
  const head = showRate ? `${code.code}: ${rate} %` : code.code;
  if (rest === '') return head;
  return showRate ? `${head} – ${rest}` : `${head}: ${rest}`;
}

/**
 * Whether a code of `kind` goes with a payment on `side` (a category's `books_as`).
 * Output VAT is a sale's, input VAT and reverse charge a purchase's, and a
 * code with no kind (0) is either. No side yet means every code.
 */
export function fitsSide(kind: TaxCodeKind | null | undefined, side: Direction | null): boolean {
  if (side === null || kind == null) return true;
  return (kind === 'utgaaende') === (side === 'inn');
}

export interface TaxCodeGroup {
  /** Null for the codes that stand above the groups: 0, and any the backend gave no kind. */
  kind: TaxCodeKind | null;
  label: string | null;
  codes: TaxCode[];
}

/** The codes in their groups, each keeping the order the backend sent. Empty groups are left out. */
export function taxCodeGroups(codes: readonly TaxCode[]): TaxCodeGroup[] {
  const groups: TaxCodeGroup[] = [
    { kind: null, label: null, codes: codes.filter((code) => code.kind == null) },
    ...GROUPS.map(({ kind, label }) => ({
      kind,
      label,
      codes: codes.filter((code) => code.kind === kind),
    })),
  ];
  return groups.filter((group) => group.codes.length > 0);
}

/** Fill a select with the codes, grouped. Whatever it held is gone; set `value` after. */
export function fillTaxSelect(select: HTMLSelectElement, codes: readonly TaxCode[]): void {
  select.replaceChildren();
  for (const group of taxCodeGroups(codes)) {
    let parent: HTMLSelectElement | HTMLOptGroupElement = select;
    if (group.label !== null) {
      parent = document.createElement('optgroup');
      parent.label = group.label;
      if (group.kind !== null) parent.dataset.kind = group.kind;
      select.append(parent);
    }
    for (const code of group.codes) {
      const node = document.createElement('option');
      node.value = code.code;
      node.textContent = taxCodeLabel(code);
      parent.append(node);
    }
  }
}

/**
 * Show only the groups for `side`, or all of them for null. On the options as
 * well as their group, because a browser that draws its own list may skip a
 * hidden group's label and still list what is in it.
 */
export function narrowTaxSelect(select: HTMLSelectElement, side: Direction | null): void {
  for (const group of select.querySelectorAll<HTMLOptGroupElement>('optgroup[data-kind]')) {
    const visible = fitsSide(group.dataset.kind as TaxCodeKind, side);
    group.hidden = !visible;
    for (const node of group.querySelectorAll('option')) node.hidden = !visible;
  }
}
