/**
 * The programs a person can say they are moving from on /tidligere-program/,
 * and which one the page picks before they say.
 *
 * Every program's books come over the same way, as SAF-T files, so the choice
 * only ever adds: the move guide for that program, and for Tripletex the card
 * for its printout of the vouchers, the one place the documents behind the
 * vouchers come from (docs/integrations/saf-t.md). With that card on the page
 * for everyone, a person moving from Fiken read about a printout they cannot
 * make, next to the upload they came for.
 */

import type { DocumentImport, SaftImport } from './api';

export interface Program {
  /** In `?fra=`. A program with its own move guide shares the guide's slug. */
  slug: string;
  /** In the dropdown and in running text. */
  name: string;
  /** The move guide under /kom-i-gang/ that says where its SAF-T export is. */
  guide: string;
  /** Whether Myntos reads this program's printout of the vouchers with their attachments. */
  printout: boolean;
  /**
   * The program's own export with every year's SAF-T file in one ZIP, and its
   * file name, when there is one: the upload reads such a ZIP as it is
   * (backend `saft/intake.py`). Said on the page only for that program; the
   * ZIP is read whatever was chosen.
   */
  completeExport?: string;
  /**
   * Lower case, looked for in the SAF-T header's SoftwareCompanyName
   * («Tripletex»). Left out where the name is shared: Visma's programs, and
   * the banks' and Uni Economy's, which are all Unimicro underneath.
   */
  software?: string;
}

/** The program for everything not in the list: its guide says how to find the file anywhere. */
export const OTHER = 'annet-program';

/** Alphabetical, so a name is found where it is looked for, and «Et annet program» last. */
export const PROGRAMS: readonly Program[] = [
  { slug: '24sevenoffice', name: '24SevenOffice', guide: OTHER, printout: false, software: '24sevenoffice' },
  { slug: 'conta', name: 'Conta', guide: 'conta', printout: false, software: 'conta' },
  { slug: 'dnb-regnskap', name: 'DNB Regnskap', guide: OTHER, printout: false },
  { slug: 'duett', name: 'Duett', guide: OTHER, printout: false, software: 'duett' },
  { slug: 'fiken', name: 'Fiken', guide: 'fiken', printout: false, software: 'fiken', completeExport: 'fiken-komplett-….zip' },
  { slug: 'poweroffice-go', name: 'PowerOffice Go', guide: 'poweroffice-go', printout: false, software: 'poweroffice' },
  { slug: 'sparebank1-regnskap', name: 'SpareBank 1 Regnskap', guide: OTHER, printout: false },
  { slug: 'tripletex', name: 'Tripletex', guide: 'tripletex', printout: true, software: 'tripletex' },
  { slug: 'uni-economy', name: 'Uni Economy', guide: OTHER, printout: false },
  { slug: 'visma-business', name: 'Visma Business', guide: OTHER, printout: false },
  { slug: 'visma-eaccounting', name: 'Visma eAccounting', guide: OTHER, printout: false },
  { slug: 'xledger', name: 'Xledger', guide: OTHER, printout: false, software: 'xledger' },
  { slug: OTHER, name: 'Et annet program', guide: OTHER, printout: false },
];

const BY_SLUG = new Map(PROGRAMS.map((program) => [program.slug, program]));

export function programBySlug(slug: string | null | undefined): Program | null {
  return (slug && BY_SLUG.get(slug)) || null;
}

/** The program a SAF-T file says made it, when its name says so alone. */
export function programFromSoftware(software: string | null): Program | null {
  if (software === null) return null;
  const name = software.toLowerCase();
  return PROGRAMS.find((program) => program.software !== undefined && name.includes(program.software)) ?? null;
}

/**
 * The program the page starts with, before the person chooses.
 *
 * What was asked for comes first: `?fra=` is put there by the move guide they
 * came from (/kom-i-gang/tripletex/ links with ?fra=tripletex), and by the page
 * itself when they choose, so a reload keeps it. Then an upload of printouts,
 * which only the program that prints can have made, and which must not vanish
 * behind the page's guess. Then what the SAF-T files already read say made
 * them, newest upload first, so a person coming back to books brought over
 * from Tripletex finds the card where they left it. Otherwise nothing: a
 * guess would show a card about another program's files.
 */
export function initialProgram(options: {
  requested: string | null;
  printouts: DocumentImport[];
  imports: SaftImport[];
}): Program | null {
  const requested = programBySlug(options.requested);
  if (requested !== null) return requested;
  if (options.printouts.length > 0) return PROGRAMS.find((program) => program.printout) ?? null;
  for (const job of options.imports) {
    const program = programFromImport(job);
    if (program !== null) return program;
  }
  return null;
}

/** The program an import's files say made them, once they are read. */
export function programFromImport(job: SaftImport): Program | null {
  if (job.purpose !== 'import') return null;
  const review = job.result ?? (job.review?.kind === 'import' ? job.review : null);
  for (const year of review?.years ?? []) {
    const program = programFromSoftware(year.software);
    if (program !== null) return program;
  }
  return null;
}

/** Said when the page changed the choice because the files named another program. */
export function switchedNote(program: Program): string {
  return `Filene er fra ${program.name}, så ${program.name} er valgt som programmet du kommer fra.`;
}

/**
 * The line under the upload for a program whose complete export the upload
 * reads as it is, or null. Fiken's: the person need not unpack it or pick the
 * SAF-T files out, and the documents in it are kept with the books (since
 * 2026-10-27; before, this line told them to keep the export themselves).
 */
export function exportNote(program: Program | null): string | null {
  if (program?.completeExport === undefined) return null;
  return (
    `Fra ${program.name} kan du laste opp den komplette eksporten (${program.completeExport}) ` +
    'slik den er. Myntos tar ut SAF-T-filen for hvert år, og når du fører, tar Myntos vare på ' +
    'kvitteringene, fakturaene og resten av filene i den, med mappen de lå i.'
  );
}

/**
 * Whether the printout card is on the page: when the chosen program prints.
 *
 * The choice alone decides. Keeping the card for an upload not yet archived,
 * whatever was chosen, kept it on screen after a person chose Fiken, which
 * reads as a dropdown that does nothing; an upload waiting for its button is
 * found anyway, since it makes the page start with Tripletex chosen
 * (`initialProgram`).
 */
export function showsPrintouts(program: Program | null): boolean {
  return program?.printout === true;
}

/** The program in running text: «Fiken», or «det gamle programmet» for the rest. */
function called(program: Program): string {
  return program.slug === OTHER ? 'det gamle programmet' : program.name;
}

/** The link to the move guide, which says where the SAF-T export is. */
export function guideLink(program: Program): { href: string; text: string } {
  const text =
    program.guide === OTHER
      ? `Slik finner du SAF-T-filene i ${called(program)}`
      : `Slik tar du SAF-T-filene ut av ${program.name}`;
  return { href: `/kom-i-gang/${program.guide}/`, text };
}

/** What becomes of the documents behind the vouchers, the part SAF-T does not carry. */
export function documentsNote(program: Program): string {
  if (program.printout) {
    return (
      'Kvitteringene og fakturaene bak bilagene er ikke i SAF-T-filene. Dem henter du fra ' +
      `utskriften av bilagene i ${program.name}, i kortet «Vedlegg fra ${program.name}» under.`
    );
  }
  if (program.completeExport !== undefined) {
    // Kept, but on no voucher: the export does not say which voucher each
    // document is behind (backend saft/archive.py).
    return (
      'Kvitteringene og fakturaene bak bilagene er ikke i SAF-T-filene, men de er i den komplette ' +
      `eksporten fra ${program.name}. Laster du opp den, tar Myntos vare på dem med mappen de lå ` +
      'i. De legges ikke på hvert sitt bilag.'
    );
  }
  return (
    'Kvitteringene og fakturaene bak bilagene er ikke i SAF-T-filene, og Myntos kan bare hente ' +
    `dem fra Tripletex. Ta vare på bilagsarkivet fra ${called(program)}.`
  );
}
