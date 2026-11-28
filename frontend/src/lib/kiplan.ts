/**
 * What /ki/about/ says about a company's KI subscription, weekly quota (%),
 * invoices, and legacy Mollie checkout return.
 *
 * Kept out of the page so every sentence can be unit-tested: they carry dates,
 * weekly quota percentages, and billing terms that must match the backend.
 */

import type { KiBillingInvoice, KiPeriod, KiStatus } from './api';
import { formatDay, formatPercent } from './kiinput';

/** What the page says when the browser comes back from Mollie. */
export interface PurchaseOutcome {
  text: string;
  tone: 'ok' | 'error';
  /** False while Mollie has not decided yet, so the page asks again. */
  settled: boolean;
}

/** Said when Mollie still had not decided after the page stopped asking. */
export const STILL_OPEN =
  'Mollie har ikke bekreftet betalingen ennå. Måneden kommer på plass av seg selv når de gjør ' +
  'det. Last siden på nytt om litt.';

const TEST_NOTE = ' Betalt i testmodus: ingen penger er trukket.';

function fromDay(iso: string | null, now: Date): string {
  const day = formatDay(iso);
  return day !== '' && day === formatDay(now.toISOString()) ? 'fra i dag' : `fra ${day}`;
}

export function purchaseOutcome(period: KiPeriod, now: Date = new Date()): PurchaseOutcome {
  switch (period.status) {
    case 'paid':
      return {
        text:
          `${period.plan_name} er betalt, og gjelder ${fromDay(period.starts_at, now)} til ` +
          `${formatDay(period.ends_at)}.${period.test ? TEST_NOTE : ''}`,
        tone: 'ok',
        settled: true,
      };
    case 'open':
      return {
        text: 'Venter på at Mollie bekrefter betalingen …',
        tone: 'ok',
        settled: false,
      };
    case 'canceled':
      return { text: 'Du avbrøt betalingen, og ingenting er trukket.', tone: 'error', settled: true };
    case 'expired':
      return {
        text: 'Betalingen ble ikke fullført i tide, og ingenting er trukket. Prøv igjen under.',
        tone: 'error',
        settled: true,
      };
    case 'failed':
      return {
        text: 'Betalingen gikk ikke gjennom, og ingenting er trukket. Prøv igjen under.',
        tone: 'error',
        settled: true,
      };
  }
}

export function returnedReference(search: string): string | null {
  const value = new URLSearchParams(search).get('betaling');
  return value !== null && /^[A-Za-z0-9_-]{8,64}$/.test(value) ? value : null;
}

/** Headline line for the company's current KI plan and weekly quota (%). */
export function monthLine(status: KiStatus): string {
  const sub = status.subscription;
  const weekly = status.weekly;
  if (sub !== null && weekly !== null) {
    const resetDay = formatDay(weekly.resets_at);
    const periodEnd = formatDay(sub.current_period_end);
    const renewal = sub.cancel_at_period_end
      ? ` Avsluttes ${periodEnd}.`
      : ` Neste fakturaperiode starter ${periodEnd}.`;
    if (weekly.remaining_percent <= 0) {
      return (
        `${sub.plan_name}: ukekvoten er brukt opp (0 % igjen). ` +
        `«Gratis» svarer til mandag ${resetDay}.${renewal}`
      );
    }
    return (
      `${sub.plan_name}: ${formatPercent(weekly.remaining_percent)} igjen denne uken ` +
      `(nullstilles mandag ${resetDay}).${renewal}`
    );
  }

  const current = status.current;
  if (current === null) return 'Selskapet har ingen KI-plan nå. «Gratis» er alltid med, uten grense.';
  const until = formatDay(current.ends_at);
  const test = current.test ? TEST_NOTE : '';
  if (weekly !== null && weekly.remaining_percent <= 0 && current.remaining_percent > 0) {
    return (
      `${current.plan_name}: ukekvoten er brukt opp (0 % igjen). ` +
      `«Gratis» svarer til mandag ${formatDay(weekly.resets_at)}.${test}`
    );
  }
  if (current.remaining_percent <= 0) {
    return (
      `${current.plan_name}: kvoten for måneden er brukt opp (0 % igjen). ` +
      `«Gratis» svarer til måneden er over, ${until}.${test}`
    );
  }
  if (weekly !== null) {
    return (
      `${current.plan_name}: ${formatPercent(weekly.remaining_percent)} igjen denne uken ` +
      `(nullstilles mandag ${formatDay(weekly.resets_at)}), til ${until}.${test}`
    );
  }
  return `${current.plan_name}: ${formatPercent(current.remaining_percent)} igjen, til ${until}.${test}`;
}

/** Months already paid for (Mollie) that have not started, or null when none. */
export function nextLine(status: KiStatus): string | null {
  const [first, ...rest] = status.upcoming;
  if (first === undefined) return null;
  if (rest.length === 0) {
    return `Neste måned er betalt: ${first.plan_name}, fra ${formatDay(first.starts_at)}.`;
  }
  return `${rest.length + 1} måneder til er betalt. Den første starter ${formatDay(first.starts_at)}.`;
}

function lastPaidEnd(status: KiStatus): string | null {
  const months = [status.current, ...status.upcoming].filter(
    (period): period is KiPeriod => period !== null && period.ends_at !== null,
  );
  const last = months[months.length - 1];
  return last === undefined ? null : last.ends_at;
}

/** Whether the owner can activate or switch a monthly invoiced subscription. */
export function mayManageSubscription(status: KiStatus): boolean {
  return status.subscription_available && status.can_manage_subscription && status.ki_enabled;
}

/** Whether the plan action buttons are shown (monthly subscription or legacy Mollie). */
export function mayBuy(status: KiStatus): boolean {
  return status.plans_on_sale && status.can_buy && status.ki_enabled;
}

/** The line under the plans explaining billing, permissions, or KI-off state. */
export function buyNote(status: KiStatus, companyName: string): string {
  if (!status.plans_on_sale) return 'KI-planer tilbys ikke i denne installasjonen av Myntos.';
  if (!status.can_buy) return `Bare eieren av ${companyName} kan velge eller endre en KI-plan.`;
  if (!status.ki_enabled) {
    const end = status.subscription?.current_period_end ?? status.current?.ends_at ?? null;
    const running =
      end === null ? '' : ` Perioden som allerede løper, varer til ${formatDay(end)}.`;
    return `KI er slått av for ${companyName}. Slå på KI under Innstillinger for selskapet før du velger en plan.${running}`;
  }
  if (status.subscription_available) {
    const sub = status.subscription;
    if (sub !== null && sub.cancel_at_period_end) {
      return (
        `Abonnementet for ${companyName} er sagt opp og avsluttes ${formatDay(sub.current_period_end)}. ` +
        'Velger du en plan nå, fortsetter abonnementet videre uten ny faktura før neste periode.'
      );
    }
    if (sub !== null) {
      return (
        `Du kan bytte plan når som helst for ${companyName}. Ny ukekvote gjelder med en gang, ` +
        `og ny pris kommer først på neste månedlige faktura (${formatDay(sub.current_period_end)}).`
      );
    }
    return (
      `Velger du en plan for ${companyName}, starter den med en gang med en ukentlig kvote ` +
      'som nullstilles hver mandag. Første månedsfaktura sendes som EHF (når selskapet mottar EHF) og på e-post.'
    );
  }
  const end = lastPaidEnd(status);
  if (end !== null) {
    return (
      `En måned du kjøper nå, gjelder ${companyName} og starter ${formatDay(end)}, ` +
      `når den dere har betalt for er over.`
    );
  }
  return `En måned du kjøper nå, gjelder ${companyName} og starter når betalingen er gjennomført.`;
}

/** Human-readable Norwegian delivery status for one KI invoice row. */
export function deliveryLabel(invoice: KiBillingInvoice): string {
  if (invoice.delivery_status === 'failed') return 'Sending feilet (PDF klar her)';
  if (invoice.delivery_method === 'ehf') {
    return invoice.delivery_status === 'delivered' ? 'Levert som EHF' : 'Sendt som EHF';
  }
  if (invoice.delivery_method === 'email') return 'Sendt på e-post';
  return 'Klar for nedlasting';
}
