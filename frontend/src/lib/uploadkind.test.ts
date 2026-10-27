/**
 * Where a file dropped on /tidligere-program/ goes: the right upload, or
 * nowhere with a sentence saying what it is. The files are the ones a person
 * moving from Fiken or Tripletex actually has in their downloads.
 */

import { describe, expect, it } from 'vitest';

import { programBySlug } from './programs';
import { classifyHead, place, type UploadKind } from './uploadkind';

const encode = (text: string): Uint8Array => new TextEncoder().encode(text);

const SAFT = encode(
  '<?xml version="1.0" encoding="UTF-8"?>\n<!-- SAF-T fra Fiken -->\n' +
    '<n1:AuditFile xmlns:n1="urn:StandardAuditFile-Taxation-Financial:NO"><n1:Header>' +
    '<n1:AuditFileVersion>1.30</n1:AuditFileVersion>' +
    '<n1:SoftwareCompanyName> Fiken AS </n1:SoftwareCompanyName></n1:Header>',
);
const PDF = encode('%PDF-1.7\n%âãÏÓ');
const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x14, 0x00]);
const CAMT = encode(
  '<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">',
);
const EHF = encode('<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">');

const fiken = programBySlug('fiken');
const tripletex = programBySlug('tripletex');

describe('classifyHead', () => {
  it('reads a SAF-T file and the program that made it from its header', () => {
    expect(classifyHead('saft-gard-2025.xml', SAFT)).toEqual({ kind: 'saft', software: 'Fiken AS' });
  });

  it('reads a UTF-16 SAF-T file by its byte order mark', () => {
    const text = '<?xml version="1.0" encoding="UTF-16"?><AuditFile>';
    const bytes = new Uint8Array(2 + text.length * 2);
    bytes.set([0xff, 0xfe]);
    for (let i = 0; i < text.length; i++) bytes[2 + i * 2] = text.charCodeAt(i);
    expect(classifyHead('saft.xml', bytes)).toEqual({ kind: 'saft', software: null });
  });

  it('tells a printout, a ZIP, Fiken’s complete export and a workbook apart', () => {
    expect(classifyHead('Bilag.pdf', PDF)).toEqual({ kind: 'printout' });
    expect(classifyHead('SAF-T Financial_1_1.zip', ZIP)).toEqual({ kind: 'zip', fikenExport: false });
    expect(classifyHead('fiken-komplett-gard.zip', ZIP)).toEqual({ kind: 'zip', fikenExport: true });
    expect(classifyHead('fiken-regnskap-gard-2025.xlsx', ZIP)).toEqual({ kind: 'spreadsheet' });
  });

  it('names other XML by its document element and namespace', () => {
    expect(classifyHead('konto.xml', CAMT)).toEqual({
      kind: 'other-xml',
      element: 'Document',
      namespace: 'urn:iso:std:iso:20022:tech:xsd:camt.053.001.02',
    });
    expect(classifyHead('faktura.xml', EHF)).toEqual({
      kind: 'other-xml',
      element: 'Invoice',
      namespace: 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    });
    expect(classifyHead('notat.txt', encode('Saldobalanse 2025'))).toEqual({ kind: 'unknown' });
    expect(classifyHead('halv.xml', encode('<?xml version="1.0"'))).toEqual({ kind: 'unknown' });
  });
});

describe('place', () => {
  const saft: UploadKind = { kind: 'saft', software: 'Fiken AS' };

  it('keeps a SAF-T file where it was dropped, and names the program it came from', () => {
    const placed = place('2025.xml', saft, 'saft', tripletex);
    expect(placed.box).toBe('saft');
    expect(placed.note).toBeNull();
    expect(placed.program?.slug).toBe('fiken');
    expect(place('2025.xml', saft, 'saft', fiken).program).toBeNull();
  });

  it('moves a SAF-T file dropped on the printout to the SAF-T files, and says so', () => {
    const placed = place('2025.xml', saft, 'printout', tripletex);
    expect(placed.box).toBe('saft');
    expect(placed.note).toContain('lagt til SAF-T-filene over');
  });

  it('takes Fiken’s complete export as SAF-T files, and Fiken as the program', () => {
    const placed = place('fiken-komplett-gard.zip', { kind: 'zip', fikenExport: true }, 'saft', null);
    expect(placed).toEqual({ box: 'saft', note: null, program: fiken });
  });

  it('moves a printout dropped on the SAF-T files to Tripletex’s card, choosing Tripletex', () => {
    const placed = place('Bilag.pdf', { kind: 'printout' }, 'saft', null);
    expect(placed.box).toBe('printout');
    expect(placed.note).toContain('«Vedlegg fra Tripletex»');
    expect(placed.program?.slug).toBe('tripletex');
    expect(place('Bilag.pdf', { kind: 'printout' }, 'printout', tripletex)).toEqual({
      box: 'printout',
      note: null,
      program: null,
    });
  });

  it('leaves a PDF out for a program whose printout Myntos does not read', () => {
    const placed = place('kvittering.pdf', { kind: 'printout' }, 'saft', fiken);
    expect(placed.box).toBeNull();
    expect(placed.note).toContain('Ta vare på dem fra Fiken selv');
  });

  it('says what a file that belongs nowhere here is', () => {
    expect(place('r.xlsx', { kind: 'spreadsheet' }, 'saft', null).note).toContain('regneark');
    const camt: UploadKind = {
      kind: 'other-xml',
      element: 'Document',
      namespace: 'urn:iso:std:iso:20022:tech:xsd:camt.053.001.02',
    };
    expect(place('konto.xml', camt, 'saft', null).note).toContain('kontoutskrift');
    const ehf: UploadKind = { kind: 'other-xml', element: 'Invoice', namespace: '' };
    expect(place('f.xml', ehf, 'saft', null).note).toContain('EHF-faktura');
    expect(place('x.bin', { kind: 'unknown' }, 'printout', null).note).toContain('ikke en PDF');
    expect(place('x.bin', { kind: 'unknown' }, 'saft', null).box).toBeNull();
  });
});
