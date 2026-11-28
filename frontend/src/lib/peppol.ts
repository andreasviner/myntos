/**
 * What the pages say about sending and receiving EHF over Peppol.
 *
 * Read by /faktura/, which sends and shows how a send stands, by
 * /organisasjon/, which registers the company and toggles receiving, and by
 * /innkommende/, which lists and books received supplier EHF invoices.
 * Kept in one place so every page and help article names each step the same way.
 *
 * Sending is the default, not a second step: from 1 January 2027 an invoice to
 * another bokføringspliktig is only delivered when it travels as EHF, so the
 * review's main button issues and sends in one press whenever the customer can
 * receive (`delivery`).
 */

import type { EhfSending, IncomingEhfStatus, PeppolStanding } from './api';
import { isoDate } from './format';

/** The button on an issued invoice. */
export const SEND_LABEL = 'Send som EHF';
/** The button on an invoice whose send failed. */
export const SEND_AGAIN_LABEL = 'Send EHF på nytt';
/** The review's main button when the invoice goes as EHF the moment it is issued. */
export const ISSUE_AND_SEND_LABEL = 'Utsted og send som EHF';
/** Beside it, for an invoice the person wants issued but not sent yet. */
export const ISSUE_ONLY_LABEL = 'Utsted uten å sende';
/** The company page's button to register for sending EHF. */
export const REGISTER_LABEL = 'Registrer for e-faktura';
/** The invoice page's one-click button to activate EHF sending before the first invoice. */
export const ACTIVATE_LABEL = 'Aktiver e-faktura (EHF)';
/** The company page's buttons to turn Peppol receiving (ELMA/SMP) on or off. */
export const ENABLE_RECEIVING_LABEL = 'Slå på mottak av e-faktura';
export const DISABLE_RECEIVING_LABEL = 'Slå av mottak av e-faktura';

/** The status column's word for a sent invoice. */
export function sendingLabel(sending: EhfSending | null): string | null {
  if (sending === null) return null;
  if (sending.status === 'delivered') return 'Levert som EHF';
  if (sending.status === 'failed') return 'EHF kom ikke fram';
  return 'Sendt som EHF';
}

/** One sentence about a send, for the invoice that was just sent or opened. */
export function sendingSentence(sending: EhfSending): string {
  if (sending.status === 'delivered') {
    return 'EHF-en er levert til kundens system.';
  }
  if (sending.status === 'failed') {
    const why = sending.failure_message ? ` Tilgangspunktet sa: «${sending.failure_message}».` : '';
    return `EHF-en kom ikke fram til kunden.${why} Du kan sende den på nytt, eller sende PDF-en selv.`;
  }
  return 'EHF-en er sendt over Peppol. Vi venter på kvittering fra kundens system.';
}

/** Status label for an incoming EHF document in the inbox. */
export function incomingStatusLabel(status: IncomingEhfStatus): string {
  if (status === 'bokfort') return 'Bokført';
  if (status === 'avvist') return 'Avvist';
  return 'Ny';
}

/**
 * Whether to offer the send button on a document.
 *
 * A failed send is offered again; a pending or delivered one never is, because
 * an invoice that arrives twice can be paid twice.
 */
export function sendable(
  item: { status: string; has_ehf: boolean; buyer_orgnr: string | null; ehf_sending: EhfSending | null },
  standing: PeppolStanding | null,
): boolean {
  if (standing === null || !standing.can_send) return false;
  if (item.status !== 'utstedt' || !item.has_ehf || !item.buyer_orgnr) return false;
  return item.ehf_sending === null || item.ehf_sending.status === 'failed';
}

/**
 * Whether the invoice page should prompt the user to set up EHF before
 * creating and sending their first business invoice.
 *
 * True whenever the deployment has a Peppol access point configured and this
 * company cannot send yet. Private-consumer (B2C) invoices remain available
 * via an explicit opt-out link on the setup card.
 */
export function needsSetupGate(standing: PeppolStanding | null): boolean {
  return standing !== null && standing.available && !standing.can_send;
}

export type Delivery = 'ehf' | 'not-on-peppol' | 'not-registered' | 'pdf';

export function delivery(
  draft: { buyer_orgnr: string | null; buyer_is_business: boolean },
  standing: PeppolStanding | null,
  receives: boolean | null,
): Delivery {
  if (standing === null || !standing.available) return 'pdf';
  if (!standing.can_send) return draft.buyer_is_business ? 'not-registered' : 'pdf';
  if (!draft.buyer_orgnr) return 'pdf';
  return receives === false ? 'not-on-peppol' : 'ehf';
}

export function deliverySentence(
  how: Delivery,
  draft: { buyer_name: string; buyer_orgnr: string | null },
  mandatoryFrom: string,
): string | null {
  if (how === 'ehf') {
    return (
      `Når den er utstedt, sendes den som EHF over Peppol til ${draft.buyer_name} ` +
      `(${draft.buyer_orgnr ?? ''}), rett inn i kundens regnskapssystem. En faktura som er ` +
      'sendt, kan ikke kalles tilbake.'
    );
  }
  if (how === 'not-on-peppol') {
    return (
      `${draft.buyer_name} kan ikke ta imot EHF over Peppol, så fakturaen sendes ikke herfra. ` +
      'Last ned PDF-en når den er utstedt, og send den selv.'
    );
  }
  if (how === 'not-registered') {
    return (
      `Fra ${isoDate(mandatoryFrom)} skal fakturaer til andre bokføringspliktige sendes som ` +
      'EHF over Peppol, og e-faktura er standard i Myntos. Sett opp e-faktura før du kan ' +
      'utstede faktura til en bedrift.'
    );
  }
  return null;
}

/**
 * The company page's sentence for where the company stands on sending EHF.
 *
 * `null` when the deployment has no access point: there is then no step a
 * person can take, and the card is not shown.
 */
export function standingSentence(standing: PeppolStanding): string | null {
  if (!standing.available) return null;
  if (standing.can_send) {
    return (
      'Selskapet er registrert for sending. Fakturaer til kunder på Peppol sendes som EHF når ' +
      'de utstedes på Faktura-siden.'
    );
  }
  if (!standing.company_confirmed) {
    return (
      'For å sende eller motta EHF må selskapet først bekreftes med ID-porten, så bare de som ' +
      'kan handle for selskapet kan sende og motta fakturaer i dets navn.'
    );
  }
  return (
    'Selskapet er ikke registrert for e-faktura ennå. Registrer det for å sende fakturaer som ' +
    'EHF over Peppol. Selve registreringen er bare for å sende, så e-fakturaer du mottar i dag, ' +
    'kommer fram som før.'
  );
}

/** The company page's sentence for receiving supplier EHFs over Peppol (ELMA). */
export function receivingSentence(standing: PeppolStanding): string | null {
  if (!standing.available || !standing.company_confirmed) return null;
  if (standing.can_receive) {
    return (
      'Mottak av e-faktura er slått på. Leverandørfakturaer sendt til selskapets ' +
      'organisasjonsnummer over Peppol kommer inn under Innkommende e-faktura.'
    );
  }
  return (
    'Mottak av e-faktura er slått av. Å slå det på registrerer organisasjonsnummeret i ' +
    'Peppol-nettverket (ELMA) gjennom Myntos. Hvis selskapet allerede mottar e-faktura i et ' +
    'annet regnskapsprogram, må mottak slås av der først.'
  );
}
