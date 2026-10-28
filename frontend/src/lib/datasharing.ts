/**
 * What each level of sharing with Myntos covers, in one place.
 *
 * Read by the choice under Innstillinger (/organisasjon/#datadeling) and by
 * the page that explains it (/datadeling/), so the two cannot describe a level
 * differently. What is written here is the promise: docs/data-sharing.md says
 * a level may only ever be narrowed without asking again, never widened, and
 * the backend's `myntos.domain.data_sharing` is what a collector checks.
 *
 * Each level shares everything the one before it does. The last is the bilag
 * themselves, as they are, and it is the only one that is not anonymous, so it
 * is the only one marked `danger`: the page draws it in red and asks for a tick
 * before it can be saved.
 */

import type { DataSharingLevel } from './api';

export interface SharingLevel {
  value: DataSharingLevel;
  /** The option's text in the list. */
  label: string;
  /** One sentence, under the list while this level is chosen. */
  summary: string;
  /** What is shared at this level, beyond the level before it. */
  shares: readonly string[];
  /** What stays out, said as plainly as what goes in. */
  keeps: readonly string[];
  /** Not anonymised. Red on the page, and saved only after a tick. */
  danger: boolean;
}

const NOTHING: SharingLevel = {
  value: 'none',
  label: 'Ingenting',
  summary: 'Myntos bruker ingenting fra selskapet til å gjøre Myntos bedre. Dette er standard.',
  shares: [],
  keeps: ['Alt. Regnskapet brukes bare til å føre regnskapet ditt.'],
  danger: false,
};

export const LEVELS: readonly SharingLevel[] = [
  NOTHING,
  {
    value: 'usage',
    label: 'Bruksstatistikk',
    summary:
      'Hvilke sider og funksjoner som brukes, og hva som går tregt eller feiler i ' +
      'nettleseren. Ingenting fra regnskapet.',
    shares: [
      'Hvilke sider og knapper som brukes, og hvor ofte.',
      'Hvor lang tid det tar å laste en side og å lese et bilag.',
      // I nettleseren, fordi feil på serveren lagres for alle uansett nivå
      // (docs/operator.md). Sto det bare «hva som feiler», ville det se ut som
      // om de ble borte på «Ingenting».
      'Hva som feiler i nettleseren din, og hvor i programmet.',
    ],
    keeps: [
      'Alt som står i regnskapet: beløp, navn, tekst og dokumenter.',
      'Hvem som gjorde hva. Det knyttes ikke til deg eller til selskapet.',
    ],
    danger: false,
  },
  {
    value: 'anonymised',
    label: 'Anonymiserte rettelser',
    summary:
      'Bruksstatistikk, og hvordan forslagene fra Myntos ble rettet, uten navn, beløp ' +
      'eller dokumenter.',
    shares: [
      'Hvilken kategori, konto og mva-kode Myntos foreslo for et bilag, og hva som ble valgt i stedet.',
      'Hva slags bilag det var: kvittering, faktura, skjermbilde eller kontoutskrift.',
      'Næringskoden til selskapet, som er offentlig, så vi ser hvilke bransjer forslagene treffer dårligst for.',
    ],
    keeps: [
      'Navn, adresser, organisasjonsnumre og kontonumre.',
      'Beløp, beskrivelser og annen tekst som er skrevet inn.',
      'Selve bilagene.',
      'Hvem som gjorde hva. Det knyttes ikke til deg eller til selskapet.',
    ],
    danger: false,
  },
  {
    value: 'everything',
    label: 'Alt, også bilag',
    summary:
      'Bilagene og alt som er ført om dem, slik de er. Dette er ikke anonymisert, og det ' +
      'kan inneholde personopplysninger om kunder, leverandører og ansatte.',
    shares: [
      'Bilagene selv: kvitteringer, fakturaer, skjermbilder og PDF-er.',
      'Teksten Myntos leste av dem, og forslaget Myntos laget.',
      'Beskrivelsen, posteringene, motparten og mva-koden slik bilaget ble ført.',
      'Navn, adresser, beløp og kontonumre som står på bilagene, også om andre enn deg.',
    ],
    keeps: [
      'Det leses bare av dem som lager Myntos, og lagres i EØS.',
      'Det brukes ikke til å trene noen KI-modell. Vi bruker det til å teste at Myntos leser bilag riktig.',
      'Det slettes når selskapet velger et lavere nivå, og når selskapet slettes.',
    ],
    danger: true,
  },
];

/** The level a company has, or nothing for a value this page does not know. */
export function levelOf(value: string | undefined): SharingLevel {
  // A level the backend has and this build does not is read as nothing, the
  // same way `myntos.domain.data_sharing.service.level_of` reads one the code
  // no longer knows: a yes covers what was shown, and this page did not show it.
  return LEVELS.find((level) => level.value === value) ?? NOTHING;
}

/** The sentence under the choice: what the company shares now, with its name in it. */
export function currentText(companyName: string, value: string | undefined): string {
  const level = levelOf(value);
  if (level.value === 'none') return `${companyName} deler ingenting med Myntos.`;
  if (level.danger) {
    return `${companyName} deler alt med Myntos, også bilagene, slik de er.`;
  }
  return `${companyName} deler ${level.label.toLowerCase()} med Myntos.`;
}
