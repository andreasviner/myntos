/**
 * The KI plans as /priser/ and /ki/about/ print them.
 *
 * A copy of backend/src/myntos/domain/ki/plans.py, because both pages are
 * prerendered and a price belongs in the HTML a crawler, an answer engine and a
 * signed-out visitor read, not in a request made after the page has loaded.
 * backend/tests/test_ki_plans.py reads this file and fails when the two
 * disagree, so a price changes in both places in the same commit or not at all.
 * What is charged is always the backend's: the checkout and invoice never read this.
 *
 * Amounts are decimal strings, as everywhere in Myntos, and are only formatted
 * here, never computed: the price including VAT is written out rather than
 * multiplied in JavaScript. Quotas are shown as relative tiers here and as
 * remaining percentage once active; internal NOK value budgets stay in the
 * backend.
 */

export interface PrintedKiPlan {
  /** What the API calls it: `PUT …/ki/abonnement` takes `{ plan: code }`. */
  code: string;
  name: string;
  /** Kroner per month, excluding merverdiavgift. */
  priceExclVat: string;
  /** Kroner per month, including 25 % VAT. */
  priceInclVat: string;
  /** Relative weekly quota label printed on the plan card. */
  quotaLabel: string;
  summary: string;
}

export const KI_PLANS: readonly PrintedKiPlan[] = [
  {
    code: 'start',
    name: 'KI Start',
    priceExclVat: '49.00',
    priceInclVat: '61.25',
    quotaLabel: 'Standard ukekvote',
    summary: 'Nok til omtrent hundre bilag i måneden med «Smart».',
  },
  {
    code: 'pluss',
    name: 'KI Pluss',
    priceExclVat: '149.00',
    priceInclVat: '186.25',
    quotaLabel: '3× ukekvote',
    summary: 'For et selskap med jevn drift og mange bilag.',
  },
  {
    code: 'pro',
    name: 'KI Pro',
    priceExclVat: '399.00',
    priceInclVat: '498.75',
    quotaLabel: '8× ukekvote',
    summary: 'For regnskapsførere og selskaper med mye å føre.',
  },
];

/** «49 kr», «61,25 kr»: whole kroner without the øre, as a price list reads. */
export function planPrice(amount: string): string {
  return `${amount.replace(/\.00$/, '').replace('.', ',')} kr`;
}

/** The cheapest plan's price, for a sentence that says «fra 49 kr i måneden». */
export const KI_PLANS_FROM = planPrice(KI_PLANS[0]?.priceExclVat ?? '0');
