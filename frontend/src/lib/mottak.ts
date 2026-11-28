import type {
  IncomingEhfSummary,
  IncomingMailSummary,
  SenderPolicy,
  SenderTrust,
} from './api';

export type SourceFilter = 'alle' | 'ehf' | 'epost';

export type UnifiedInboxItem =
  | {
      source: 'ehf';
      key: string;
      id: number;
      receivedAt: string;
      ehf: IncomingEhfSummary;
    }
  | {
      source: 'epost';
      key: string;
      id: number;
      receivedAt: string;
      mail: IncomingMailSummary;
    };

export type BadgeTone = 'ok' | 'warn' | 'danger' | 'muted';

export interface TrustBadgeInfo {
  label: string;
  shortLabel: string;
  tone: BadgeTone;
}

export interface AuthBadgeInfo {
  label: string;
  tone: BadgeTone;
}

/** Merge received EHF invoices and email invoices, filtered by channel and sorted newest first. */
export function mergeInboxItems(
  ehfs: readonly IncomingEhfSummary[],
  mails: readonly IncomingMailSummary[],
  source: SourceFilter,
): UnifiedInboxItem[] {
  const combined: UnifiedInboxItem[] = [];
  if (source === 'alle' || source === 'ehf') {
    for (const ehf of ehfs) {
      combined.push({
        source: 'ehf',
        key: `ehf-${ehf.id}`,
        id: ehf.id,
        receivedAt: ehf.received_at,
        ehf,
      });
    }
  }
  if (source === 'alle' || source === 'epost') {
    for (const mail of mails) {
      combined.push({
        source: 'epost',
        key: `epost-${mail.id}`,
        id: mail.id,
        receivedAt: mail.received_at,
        mail,
      });
    }
  }
  combined.sort((a, b) => {
    const cmp = b.receivedAt.localeCompare(a.receivedAt);
    if (cmp !== 0) return cmp;
    if (a.source !== b.source) return a.source === 'ehf' ? -1 : 1;
    return b.id - a.id;
  });
  return combined;
}

export function trustBadge(trust: SenderTrust): TrustBadgeInfo {
  switch (trust) {
    case 'egen_bruker':
      return { label: 'Egen bruker', shortLabel: 'Egen bruker', tone: 'ok' };
    case 'godkjent':
      return { label: 'Godkjent avsender', shortLabel: 'Godkjent', tone: 'ok' };
    case 'advarsel':
      return { label: 'Sikkerhetsvarsel', shortLabel: 'Advarsel', tone: 'danger' };
    case 'ukjent':
      return { label: 'Ukjent avsender', shortLabel: 'Ukjent', tone: 'warn' };
  }
}

export function authResultBadge(result: string | null): AuthBadgeInfo {
  switch ((result ?? '').toLowerCase()) {
    case 'pass':
      return { label: 'OK', tone: 'ok' };
    case 'fail':
      return { label: 'Feilet', tone: 'danger' };
    case 'softfail':
      return { label: 'Svak feil', tone: 'warn' };
    case 'neutral':
    case 'none':
    case 'permerror':
    case 'temperror':
      return { label: result ?? 'Ikke sjekket', tone: 'muted' };
    default:
      return { label: 'Ikke sjekket', tone: 'muted' };
  }
}

export function senderPolicyLabel(policy: SenderPolicy): string {
  return policy === 'kun_godkjente' ? 'Kun godkjente avsendere' : 'Alle med avsendervarsel';
}

export function formatSenderDisplay(mail: Pick<IncomingMailSummary, 'sender_name' | 'sender_email'>): string {
  const name = mail.sender_name?.trim();
  if (name && name.toLowerCase() !== mail.sender_email.toLowerCase()) {
    return `${name} <${mail.sender_email}>`;
  }
  return mail.sender_email;
}

export function extractSenderDomainRule(email: string): string | null {
  const idx = email.lastIndexOf('@');
  if (idx < 0 || idx === email.length - 1) return null;
  const host = email.slice(idx + 1).trim().toLowerCase();
  return host.includes('.') ? `@${host}` : null;
}

export function formatByteSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
}

export function parseSourceFilter(raw: string | null | undefined): SourceFilter {
  if (raw === 'ehf' || raw === 'epost' || raw === 'alle') return raw;
  return 'alle';
}
