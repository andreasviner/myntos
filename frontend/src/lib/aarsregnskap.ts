/**
 * Pure presentation and workflow helpers for `/aarsregnskap/`.
 *
 * Kept out of the page script so status copy, whole-krone formatting,
 * prior-year parsing, and signing preconditions are unit-tested without a DOM.
 */

import type { AarsregnskapFiling, AarsregnskapFilingStatus, AarsregnskapOverview } from './api';

const FILING_STATUS_LABELS: Record<AarsregnskapFilingStatus, string> = {
  klargjort: 'Klargjort i Altinn (ikke låst)',
  laast: 'Låst i Altinn, klar for signering',
  signert: 'Signert og sendt til Regnskapsregisteret',
  mottatt: 'Mottatt med kvittering',
  feilet: 'Feilet',
};

const DOC_TYPE_LABELS: Record<string, string> = {
  hovedskjema: 'Hovedskjema (XML)',
  underskjema: 'Selskapsregnskap (XML)',
  vedlegg_pdf: 'Vedlagt årsregnskap (PDF)',
  kvittering_pdf: 'Kvittering fra Altinn (PDF)',
  signatur_json: 'Signaturbevis (JSON)',
};

export function filingStatusLabel(status: AarsregnskapFilingStatus): string {
  return FILING_STATUS_LABELS[status] ?? status;
}

export function filingStatusTone(
  status: AarsregnskapFilingStatus,
): 'ok' | 'warn' | 'err' | 'neutral' {
  if (status === 'mottatt' || status === 'signert') return 'ok';
  if (status === 'laast' || status === 'klargjort') return 'warn';
  if (status === 'feilet') return 'err';
  return 'neutral';
}

export function documentTypeLabel(docType: string): string {
  return DOC_TYPE_LABELS[docType] ?? docType;
}

/** Format a whole NOK integer in Norwegian style (`125 000`, `-4 500`, or `-` for null). */
export function formatWholeKr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  const sign = value < 0 ? '-' : '';
  const digits = Math.abs(Math.trunc(value)).toString();
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `${sign}${grouped}`;
}

/** Parse a Norwegian whole-krone input string (`"125 000"` or `"-4500"`) into an integer or null. */
export function parseWholeKrInput(raw: string): number | null {
  const cleaned = raw.replace(/\s+/g, '').replace(/\u00a0/g, '').trim();
  if (!cleaned) return null;
  if (!/^-?\d+$/.test(cleaned)) return null;
  const parsed = Number.parseInt(cleaned, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

/** Parse a year from `#2025`, falling back to `fallback`. */
export function yearFromHash(hash: string, fallback: number): number {
  const match = /^#?(\d{4})$/.exec(hash.trim());
  if (!match) return fallback;
  const yr = Number.parseInt(match[1], 10);
  return yr >= 1980 && yr <= 2100 ? yr : fallback;
}

/** Whether a filing is currently awaiting a board member's ID-porten signature. */
export function awaitingSignature(filing: Pick<AarsregnskapFiling, 'status'>): boolean {
  return filing.status === 'laast' || filing.status === 'klargjort';
}

/** Whether a filing has been legally signed and submitted to Regnskapsregisteret. */
export function isSubmitted(filing: Pick<AarsregnskapFiling, 'status'>): boolean {
  return filing.status === 'signert' || filing.status === 'mottatt';
}

/**
 * Plain-language summary of which submission path is ready right now.
 *
 * Explains both the two-step systembruker + ID-porten flow and the direct
 * ID-porten flow without promising anything that is not configured.
 */
export function submissionReadinessText(
  data: Pick<
    AarsregnskapOverview,
    | 'submission_available'
    | 'systembruker_available'
    | 'systembruker_approved'
    | 'id_porten_available'
    | 'id_porten_logged_in'
    | 'blocked'
  >,
): string {
  if (!data.submission_available) {
    return 'Innsending til Brønnøysundregistrene er ikke satt opp i denne installasjonen ennå. Du kan fortsatt laste ned XML og PDF.';
  }
  if (data.blocked) {
    return 'Rett feilene øverst på siden før årsregnskapet kan sendes til Altinn.';
  }
  if (data.systembruker_approved && data.id_porten_logged_in) {
    return 'Altinn-systembruker og ID-porten er klare. Du kan klargjøre med systembruker eller sende og signere direkte med ID-porten.';
  }
  if (data.systembruker_approved) {
    return 'Altinn-systembruker er godkjent for klargjøring. Selve signeringen krever ID-porten fra styremedlem eller daglig leder.';
  }
  if (data.id_porten_logged_in) {
    return 'Du er logget inn med ID-porten og kan både klargjøre og signere årsregnskapet direkte i Altinn.';
  }
  if (data.id_porten_available) {
    return 'Logg inn med ID-porten for å sende og signere årsregnskapet, eller koble til Altinn-systembruker under Innstillinger for å klargjøre først.';
  }
  return 'Verken Altinn-systembruker eller ID-porten er tilgjengelig i denne installasjonen.';
}

/** Format a list of board members into newline-separated `Navn, Rolle` text for editing. */
export function formatBoardMembersText(
  members: readonly { name: string; role: string }[],
): string {
  return members.map((m) => `${m.name}, ${m.role}`).join('\n');
}

/** Parse newline-separated `Navn, Rolle` lines into board member objects. */
export function parseBoardMembersText(raw: string): { name: string; role: string }[] {
  const out: { name: string; role: string }[] = [];
  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const comma = trimmed.indexOf(',');
    if (comma === -1) {
      out.push({ name: trimmed, role: 'Styremedlem' });
    } else {
      const name = trimmed.slice(0, comma).trim();
      const role = trimmed.slice(comma + 1).trim() || 'Styremedlem';
      if (name) out.push({ name, role });
    }
  }
  return out;
}
