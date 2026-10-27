/**
 * What a file dropped on /tidligere-program/ is, from its first bytes, and
 * which of the page's uploads it belongs in.
 *
 * The page has two uploads, the SAF-T files and, for Tripletex, the printout
 * of the vouchers, and a person drops whatever they downloaded on whichever is
 * nearer: Fiken's export page alone offers four ZIPs and an Excel workbook
 * beside the SAF-T files. Rather than send the wrong file and show the
 * server's refusal, the page looks at the start of each file and puts it where
 * it belongs, or says what it is and leaves it out (docs/frontend-conventions.md,
 * «Don't warn where you can decide»). It also notices when the files say which
 * program made them, so the choice above can follow.
 *
 * The server checks the same things again (backend `saft/intake.py`), for
 * everything sent without this page; here it decides only where a file goes.
 */

import { PROGRAMS, programBySlug, programFromSoftware, type Program } from './programs';

export type UploadKind =
  /** XML whose document element is AuditFile, and who made it if the header says so early. */
  | { kind: 'saft'; software: string | null }
  /** A ZIP: a zipped SAF-T file, or an export with SAF-T files and documents in it. */
  | { kind: 'zip'; fikenExport: boolean }
  /** A PDF: the only thing the printout upload takes. */
  | { kind: 'printout' }
  | { kind: 'spreadsheet' }
  | { kind: 'other-xml'; element: string; namespace: string }
  | { kind: 'unknown' };

export type UploadBox = 'saft' | 'printout';

export interface Placement {
  /** Where the file goes, or null when it goes nowhere on this page. */
  box: UploadBox | null;
  /** What to tell the person, when the file did not stay where they put it. */
  note: string | null;
  /** The program the file says made it, when that is not the one chosen. */
  program: Program | null;
}

/** As much of a file as is read: the SAF-T header, with the program's name, is in it. */
const HEAD_BYTES = 64 * 1024;

export async function classify(file: File): Promise<UploadKind> {
  const head = new Uint8Array(await file.slice(0, HEAD_BYTES).arrayBuffer());
  return classifyHead(file.name, head);
}

export function classifyHead(name: string, head: Uint8Array): UploadKind {
  if (startsWith(head, [0x25, 0x50, 0x44, 0x46])) return { kind: 'printout' };
  if (startsWith(head, [0x50, 0x4b, 0x03, 0x04])) {
    // An Excel workbook is a ZIP too; its name is the cheap way to tell.
    if (/\.(xlsx|xlsm|ods)$/i.test(name)) return { kind: 'spreadsheet' };
    return { kind: 'zip', fikenExport: /^fiken-komplett/i.test(name) };
  }
  const text = decode(head);
  const element = documentElement(text);
  if (element === null) return { kind: 'unknown' };
  if (element.local !== 'AuditFile') {
    return { kind: 'other-xml', element: element.local, namespace: element.namespace };
  }
  return { kind: 'saft', software: softwareIn(text) };
}

/** Where a file goes when it was dropped on `droppedOn` while `chosen` is the program chosen. */
export function place(
  name: string,
  kind: UploadKind,
  droppedOn: UploadBox,
  chosen: Program | null,
): Placement {
  const differs = (program: Program | null): Program | null =>
    program !== null && program.slug !== chosen?.slug ? program : null;
  switch (kind.kind) {
    case 'saft':
    case 'zip': {
      const program =
        kind.kind === 'saft'
          ? programFromSoftware(kind.software)
          : kind.fikenExport
            ? programBySlug('fiken')
            : null;
      return {
        box: 'saft',
        note:
          droppedOn === 'printout'
            ? `«${name}» er en SAF-T-fil, ikke en utskrift av bilag. Den er lagt til SAF-T-filene over.`
            : null,
        program: differs(program),
      };
    }
    case 'printout': {
      const printer = chosen?.printout === true ? chosen : PRINTER;
      if (chosen !== null && !chosen.printout) {
        return {
          box: null,
          note:
            `«${name}» er en PDF, ikke en SAF-T-fil. Myntos henter dokumentene bak bilagene bare ` +
            `fra utskriften av bilagene i ${PRINTER.name}. Ta vare på dem fra ${chosen.name} selv.`,
          program: null,
        };
      }
      return {
        box: 'printout',
        note:
          droppedOn === 'saft'
            ? `«${name}» er en utskrift av bilag, ikke en SAF-T-fil. Den er lagt i «Vedlegg fra ${printer.name}» under.`
            : null,
        program: differs(printer),
      };
    }
    case 'spreadsheet':
      return refused(`«${name}» er et regneark, ikke en SAF-T-fil. Last opp SAF-T-filen (XML eller ZIP).`);
    case 'other-xml':
      return refused(otherXml(name, kind.element, kind.namespace));
    case 'unknown':
      return refused(
        droppedOn === 'printout'
          ? `«${name}» er ikke en PDF. Last opp utskriften av bilagene som PDF.`
          : `«${name}» er verken en SAF-T-fil (XML eller ZIP) eller en utskrift av bilag (PDF).`,
      );
  }
}

/** The one program Myntos reads a printout from: Tripletex. */
const PRINTER: Program = PROGRAMS.find((program) => program.printout) ?? PROGRAMS[0];

function refused(note: string): Placement {
  return { box: null, note, program: null };
}

function otherXml(name: string, element: string, namespace: string): string {
  if (element === 'Document' && namespace.includes('camt.05')) {
    return `«${name}» er en kontoutskrift fra banken, ikke en SAF-T-fil.`;
  }
  if (element === 'Invoice' || element === 'CreditNote') {
    return `«${name}» er en EHF-faktura, ikke en SAF-T-fil.`;
  }
  return `«${name}» er en XML-fil, men ikke en SAF-T-fil.`;
}

function startsWith(head: Uint8Array, magic: number[]): boolean {
  return magic.every((byte, index) => head[index] === byte);
}

function decode(head: Uint8Array): string {
  // SAF-T is UTF-8; a UTF-16 file says so with its byte order mark.
  if (head[0] === 0xff && head[1] === 0xfe) return new TextDecoder('utf-16le').decode(head);
  if (head[0] === 0xfe && head[1] === 0xff) return new TextDecoder('utf-16be').decode(head);
  return new TextDecoder('utf-8').decode(head);
}

/** The document element's local name and namespace, past the declaration and any comments. */
function documentElement(text: string): { local: string; namespace: string } | null {
  let rest = text.replace(/^﻿/, '');
  for (;;) {
    rest = rest.trimStart();
    const skip = rest.startsWith('<?')
      ? rest.indexOf('?>') + 2
      : rest.startsWith('<!--')
        ? rest.indexOf('-->') + 3
        : rest.startsWith('<!')
          ? rest.indexOf('>') + 1
          : 0;
    if (skip === 0) break;
    // indexOf's -1 plus the marker's length: the marker never closed.
    if (skip < 3) return null;
    rest = rest.slice(skip);
  }
  const match = /^<(?:([A-Za-z_][\w.-]*):)?([A-Za-z_][\w.-]*)([^>]*)>/.exec(rest);
  if (match === null) return null;
  const [, prefix, local, attributes] = match;
  const attribute = prefix === undefined ? 'xmlns' : `xmlns:${prefix}`;
  const escaped = attribute.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const declared = new RegExp(`${escaped}\\s*=\\s*["']([^"']*)["']`).exec(attributes);
  return { local, namespace: declared?.[1] ?? '' };
}

/** SoftwareCompanyName from the SAF-T header, which comes before anything large. */
function softwareIn(text: string): string | null {
  const match = /<(?:[\w.-]+:)?SoftwareCompanyName>\s*([^<]*?)\s*</.exec(text);
  return match === null || match[1] === '' ? null : match[1];
}
