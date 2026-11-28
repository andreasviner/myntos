/**
 * Pure presentation and workflow helpers for `/aksjonaer/` and Altinn 3
 * systembruker status on `/organisasjon/#altinn`.
 */

import type { AksjonaerYear, Shareholder, ShareholderHolding, SystemUserStatus } from './api';

export type SubmissionStep =
  | 'read_only'
  | 'not_configured'
  | 'blocked'
  | 'needs_systembruker'
  | 'ready';

const EVENT_TYPE_LABELS: Record<string, string> = {
  stiftelse: 'Stiftelse',
  nyemisjon: 'Nyemisjon',
  overdragelse: 'Overdragelse (kjøp/salg/arv/gave)',
  utbytte: 'Utbytte',
  splitt: 'Aksjesplitt',
  spleis: 'Aksjespleis',
  fondsemisjon: 'Fondsemisjon',
  kapitalforhoyelse_palydende: 'Kapitalforhøyelse (økt pålydende)',
  kapitalnedsettelse: 'Kapitalnedsettelse',
  likvidasjon: 'Likvidasjon',
};

const KIND_LABELS: Record<'person' | 'selskap' | 'utenlandsk', string> = {
  person: 'Person',
  selskap: 'Selskap',
  utenlandsk: 'Utenlandsk',
};

export function shareholderKindLabel(kind: 'person' | 'selskap' | 'utenlandsk'): string {
  return KIND_LABELS[kind] ?? kind;
}

export function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? eventType;
}

export function filingStatusLabel(status: 'sendt' | 'bekreftet' | 'feilet'): string {
  if (status === 'bekreftet') return 'Bekreftet mottatt av Skatteetaten';
  if (status === 'feilet') return 'Feilet under innsending';
  return 'Sendt (ikke bekreftet)';
}

/** Readable identifier for tables; never exposes unmasked personal national IDs. */
export function shareholderIdentifier(
  holder: Pick<
    Shareholder | ShareholderHolding,
    'kind' | 'national_id_masked' | 'orgnr' | 'foreign_id' | 'country_code'
  >,
): string {
  if (holder.kind === 'person') {
    return holder.national_id_masked ?? 'Mangler fødselsnummer';
  }
  if (holder.kind === 'selskap') {
    if (!holder.orgnr) return 'Mangler org.nr.';
    const d = holder.orgnr.replace(/\s+/g, '');
    return d.length === 9 ? `${d.slice(0, 3)} ${d.slice(3, 6)} ${d.slice(6)}` : holder.orgnr;
  }
  if (holder.foreign_id) return `${holder.foreign_id} (${holder.country_code})`;
  return `Utenlandsk (${holder.country_code})`;
}

/** Decide what stands in the way of submitting RF-1086 for the chosen year. */
export function submissionStep(
  data: Pick<AksjonaerYear, 'blocked' | 'submission_available' | 'systembruker_approved'>,
  canWrite: boolean,
): SubmissionStep {
  if (!canWrite) return 'read_only';
  if (data.blocked) return 'blocked';
  if (!data.submission_available) return 'not_configured';
  if (!data.systembruker_approved) return 'needs_systembruker';
  return 'ready';
}

/** Explicit confirmation checkbox text shown before irreversible RF-1086 submission. */
export function confirmSentence(params: {
  companyName: string;
  orgnr: string;
  year: number;
  shareClassName: string;
  shareholderCount: number;
}): string {
  const { companyName, orgnr, year, shareClassName, shareholderCount } = params;
  const form = shareholderCount === 1 ? '1 underskjema' : `${shareholderCount} underskjemaer`;
  return (
    `Jeg bekrefter at aksjonærregisteroppgaven (RF-1086) for ${companyName} (org.nr. ${orgnr}) ` +
    `for inntektsåret ${year} (${shareClassName}, hovedskjema og ${form}) er kontrollert og skal ` +
    'sendes til Skatteetaten.'
  );
}

/** Status prose for the Altinn 3 systembruker section on `/organisasjon/#altinn`. */
export function systemUserSentence(status: SystemUserStatus): string {
  if (!status.available) {
    return (
      'Altinn-systembruker er ikke satt opp i denne installasjonen ennå. Kontakt drift eller sjekk ' +
      'Maskinporten- og Altinn-oppsettet.'
    );
  }
  if (status.approved && status.system_user) {
    const labels = status.system_user.resources
      .map((res) => status.resource_labels[res] ?? res)
      .join(', ');
    return `Tilkoblingen til Altinn er godkjent. Myntos kan sende på vegne av selskapet for: ${labels}.`;
  }
  if (status.system_user) {
    const st = status.system_user.status;
    if (st === 'new') {
      return (
        'Det finnes en ventende forespørsel om systembruker i Altinn. Åpne lenken under for å ' +
        'godkjenne Myntos i Altinn, og trykk deretter «Sjekk status i Altinn».'
      );
    }
    return (
      `Forrige forespørsel om systembruker har status «${st}» i Altinn. Opprett en ny forespørsel ` +
      'hvis du vil koble selskapet til Altinn.'
    );
  }
  return (
    'For å sende aksjonærregisteroppgaven (RF-1086) direkte fra Myntos trenger selskapet en ' +
    'godkjent systembruker i Altinn. Du oppretter forespørselen her og godkjenner den en gang ' +
    'i Altinn.'
  );
}

/** Format an integer share count with Norwegian thousands spacing. */
export function formatShares(shares: number): string {
  return new Intl.NumberFormat('nb-NO', { maximumFractionDigits: 0 }).format(shares);
}
