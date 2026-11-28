import { describe, expect, it } from 'vitest';

import type { IncomingEhfSummary, IncomingMailSummary } from './api';
import {
  authResultBadge,
  extractSenderDomainRule,
  formatByteSize,
  formatSenderDisplay,
  mergeInboxItems,
  parseSourceFilter,
  senderPolicyLabel,
  trustBadge,
} from './mottak';

function makeEhf(id: number, receivedAt: string): IncomingEhfSummary {
  return {
    id,
    document_type: 'faktura',
    invoice_number: `INV-${id}`,
    issue_date: '2026-11-20',
    due_date: '2026-12-04',
    currency: 'NOK',
    net_amount: '1000.00',
    vat_amount: '250.00',
    gross_amount: '1250.00',
    seller_name: 'Leverandør AS',
    seller_orgnr: '910000004',
    seller_peppol_id: '0192:910000004',
    payment_reference: null,
    seller_bank_account: null,
    status: 'ny',
    voucher_id: null,
    has_embedded_pdf: false,
    received_at: receivedAt,
    booked_at: null,
    dismissed_at: null,
    suggested_tax_code: '1',
    suggested_party_id: null,
  };
}

function makeMail(id: number, receivedAt: string): IncomingMailSummary {
  return {
    id,
    sender_email: 'faktura@leverandor.no',
    sender_name: 'Leverandør Faktura',
    envelope_from: 'faktura@leverandor.no',
    reply_to: null,
    recipient_address: 'test@myntos.no',
    subject: `Faktura #${id}`,
    spf_result: 'pass',
    dkim_result: 'pass',
    dmarc_result: 'pass',
    sender_trust: 'godkjent',
    security_warnings: [],
    filename: `faktura-${id}.pdf`,
    content_type: 'application/pdf',
    byte_size: 20480,
    status: 'ny',
    voucher_id: null,
    received_at: receivedAt,
    booked_at: null,
    dismissed_at: null,
  };
}

describe('mottak helpers', () => {
  it('merges EHF and email items sorted newest first and filters by source toggle', () => {
    const ehfs = [makeEhf(1, '2026-11-25T09:00:00Z'), makeEhf(2, '2026-11-25T11:00:00Z')];
    const mails = [makeMail(10, '2026-11-25T10:00:00Z')];

    const all = mergeInboxItems(ehfs, mails, 'alle');
    expect(all.map((item) => item.key)).toEqual(['ehf-2', 'epost-10', 'ehf-1']);

    const onlyEhf = mergeInboxItems(ehfs, mails, 'ehf');
    expect(onlyEhf.map((item) => item.key)).toEqual(['ehf-2', 'ehf-1']);

    const onlyMail = mergeInboxItems(ehfs, mails, 'epost');
    expect(onlyMail.map((item) => item.key)).toEqual(['epost-10']);
  });

  it('maps sender trust levels and auth checks cleanly', () => {
    expect(trustBadge('egen_bruker').tone).toBe('ok');
    expect(trustBadge('godkjent').label).toBe('Godkjent avsender');
    expect(trustBadge('ukjent').tone).toBe('warn');
    expect(trustBadge('advarsel').tone).toBe('danger');

    expect(authResultBadge('pass')).toEqual({ label: 'OK', tone: 'ok' });
    expect(authResultBadge('fail')).toEqual({ label: 'Feilet', tone: 'danger' });
    expect(authResultBadge(null)).toEqual({ label: 'Ikke sjekket', tone: 'muted' });
  });

  it('formats sender display, domain rules, policy labels, sizes, and source query params', () => {
    expect(
      formatSenderDisplay({ sender_name: 'Telenor', sender_email: 'faktura@telenor.no' }),
    ).toBe('Telenor <faktura@telenor.no>');
    expect(
      formatSenderDisplay({ sender_name: null, sender_email: 'faktura@telenor.no' }),
    ).toBe('faktura@telenor.no');
    expect(extractSenderDomainRule('Faktura@Telenor.NO')).toBe('@telenor.no');
    expect(extractSenderDomainRule('invalid')).toBeNull();
    expect(senderPolicyLabel('alle_med_varsel')).toBe('Alle med avsendervarsel');
    expect(senderPolicyLabel('kun_godkjente')).toBe('Kun godkjente avsendere');
    expect(formatByteSize(512)).toBe('512 B');
    expect(formatByteSize(24576)).toBe('24 KB');
    expect(formatByteSize(1_572_864)).toBe('1,5 MB');
    expect(parseSourceFilter('ehf')).toBe('ehf');
    expect(parseSourceFilter('epost')).toBe('epost');
    expect(parseSourceFilter('noe-annet')).toBe('alle');
  });
});
