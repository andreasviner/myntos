/**
 * What the pages say about sending an invoice as EHF over Peppol.
 *
 * Read by /faktura/, which sends and shows how a send stands, and by
 * /organisasjon/, which registers the company. Kept in one place so the two
 * pages, and the help articles that quote them, name each step the same way.
 *
 * Sending is the default, not a second step: from 1 January 2027 an invoice to
 * another bokføringspliktig is only delivered when it travels as EHF, so the
 * review's main button issues and sends in one press whenever the customer can
 * receive (`delivery`). A separate «Send som EHF» after issuing was an invoice
 * issued, booked and then forgotten in the outbox, which is the one way of
 * getting the law wrong that this page could cause by itself.
 *
 * The backend decides every step (`GET …/peppol`); this only turns its answer
 * into the sentence for the first step still missing, because a person told
 * everything that is not done yet is a person who does none of it.
 */

import type { EhfSending, PeppolStanding } from './api';
import { isoDate } from './format';

/** The button on an issued invoice. */
export const SEND_LABEL = 'Send som EHF';
/** The button on an invoice whose send failed. */
export const SEND_AGAIN_LABEL = 'Send EHF på nytt';
/** The review's main button when the invoice goes as EHF the moment it is issued. */
export const ISSUE_AND_SEND_LABEL = 'Utsted og send som EHF';
/** Beside it, for an invoice the person wants issued but not sent yet. */
export const ISSUE_ONLY_LABEL = 'Utsted uten å sende';
/** The company page's button. */
export const REGISTER_LABEL = 'Registrer for e-faktura';
/** The link to the access point's identity check. */
export const VERIFY_LABEL = 'Fullfør identitetskontrollen';

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

/**
 * Whether to offer the send button on a document.
 *
 * A failed send is offered again; a pending or delivered one never is, because
 * an invoice that arrives twice can be paid twice. The backend refuses either
 * way (`already_sent`); this only keeps the button from being a trap.
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
 * How an invoice about to be issued reaches the customer.
 *
 * - `ehf`: over Peppol, sent by the same press that issues it.
 * - `not-on-peppol`: the access point found no Peppol address for this customer
 *   and document, so the person sends the PDF. A send would only be refused
 *   (`recipient_not_on_peppol`) after the invoice was issued and booked.
 * - `not-registered`: a business customer, and this company cannot send yet.
 * - `pdf`: nothing to say: a consumer or a buyer with no orgnr, or no access
 *   point in this installation.
 *
 * `receives` is the access point's answer about the customer, `null` when it
 * could not be asked. Not asked is not a no: the send then goes, and says
 * itself if the customer cannot take it, because holding back every invoice
 * while the lookup is down would make the PDF the default again.
 */
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

/**
 * The review's sentence about where the invoice goes, or `null` for nothing.
 *
 * For `ehf` it names the customer and the orgnr it is addressed to, because the
 * review is the only confirmation there is: the press that issues it sends it,
 * and a sent invoice cannot be called back.
 */
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
      'EHF over Peppol. Selskapet er ikke registrert for e-faktura ennå, så denne må du ' +
      'sende selv.'
    );
  }
  return null;
}

/**
 * The company page's sentence for where the company stands.
 *
 * Only the first missing step, each with what the person does about it.
 * `null` when the deployment has no access point: there is then no step a
 * person can take, and the card is not shown.
 */
export function standingSentence(standing: PeppolStanding): string | null {
  if (!standing.available) return null;
  if (standing.verified) {
    return (
      'Selskapet er registrert. Fakturaer til kunder på Peppol sendes som EHF når de ' +
      'utstedes på Faktura-siden.'
    );
  }
  if (standing.registered) {
    return (
      'Selskapet er registrert. Før det kan sende, må noen som signerer for selskapet ' +
      '(daglig leder, et styremedlem med signaturrett eller prokura) fullføre ' +
      'identitetskontrollen hos tilgangspunktet.'
    );
  }
  if (!standing.company_confirmed) {
    return (
      'For å sende EHF må selskapet først bekreftes med ID-porten, så bare de som kan ' +
      'handle for selskapet kan sende fakturaer i dets navn.'
    );
  }
  return (
    'Selskapet er ikke registrert for e-faktura. Registrer det for å sende fakturaer som ' +
    'EHF over Peppol. Det registreres bare for å sende, så e-fakturaer du mottar i dag, ' +
    'kommer fram som før.'
  );
}
