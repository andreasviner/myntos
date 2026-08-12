/**
 * Prisdata for bedriftsbank + regnskapsintegrasjon.
 *
 * Alle tall er hentet manuelt fra bankenes egne, offentlige prislister på den
 * datoen som står i `sjekket`. Prislister endres uten forvarsel — `kilde` peker
 * alltid på siden tallet er hentet fra, slik at leseren kan etterprøve det.
 *
 * Konvensjoner:
 * - `nettbank`    = månedspris for nettbank bedrift med én bruker, uten
 *                   integrasjon mot regnskapssystem.
 * - `nettbankApi` = total månedspris for nettbank bedrift med én bruker *og*
 *                   bankintegrasjon mot regnskapssystem (ikke et påslag).
 * - `oppstart`    = engangsgebyr for å etablere oppsettet i `nettbankApi`.
 * - Alle beløp er i NOK. `null` betyr «ikke publisert / ikke bekreftet».
 * - Priser er eks. mva der banken oppgir det.
 */

/** Hvor stor del av den reelle kostnaden som ligger utenfor månedsprisen. */
export type SkjulteGebyrer = 'Ingen' | 'Lav' | 'Middels' | 'Høy';

export interface Pris {
  /** NOK, eller null når banken ikke publiserer tallet. */
  nok: number | null;
  /** Kort presisering som vises under beløpet. */
  note?: string;
}

export interface Bank {
  id: string;
  navn: string;
  /** Tidligere navn eller fusjonsopphav — banker skifter navn, folk søker på det gamle. */
  tidligere?: string;
  nettbank: Pris;
  nettbankApi: Pris;
  oppstart: Pris;
  skjulteGebyrer: SkjulteGebyrer;
  /** Begrunnelsen for nivået over. Vises i tabellen — nivået skal aldri stå udokumentert. */
  skjulteGebyrerHvorfor: string;
  /** Støtter KID/OCR-innbetaling. Uten dette kan norsk bokføring ikke avstemmes maskinelt. */
  kid: boolean;
  /** Norsk bank/filial med norsk kontonummer, eller utenlandsk e-pengeforetak. */
  norskBank: boolean;
  /** Gebyrene som ikke ligger i månedsprisen. */
  detaljer: string[];
  kilde: string;
  kildeTekst: string;
  /** ISO-dato for når tallene sist ble kontrollert mot kilden. */
  sjekket: string;
  /** Settes når tallene ikke kunne bekreftes mot en publisert prisliste. */
  ubekreftet?: string;
}

export const SJEKKET = '2026-08-01';

export const banker: Bank[] = [
  {
    id: 'sparebank1-smn',
    navn: 'SpareBank 1 SMN',
    nettbank: { nok: 0, note: 'én bruker inkludert' },
    nettbankApi: { nok: 300, note: 'integrasjon mot økonomisystem' },
    oppstart: { nok: 0, note: 'både nettbank og integrasjon' },
    skjulteGebyrer: 'Lav',
    skjulteGebyrerHvorfor:
      'Gratis nettbank og gratis etablering. Integrasjonsprisen er ett enkelt tall, og de eneste påslagene er ekstra brukere og transaksjonsgebyr.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker: 50 kr/mnd',
      'Tilknytning annen nettbank: 90 kr per bedrift',
      'Etablering av integrasjon: 0 kr',
      'Kontoholdsgebyr: 500 kr/år — men bare for konti uten transaksjoner foregående kalenderår og innskudd under 10 000 kr, altså ikke for en bedrift i drift',
    ],
    kilde: 'https://www.sparebank1.no/nb/smn/bedrift/kundeservice/bestill/prisliste.html',
    kildeTekst: 'sparebank1.no/smn/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'dnb',
    navn: 'DNB',
    nettbank: {
      nok: 0,
      note: 'selve abonnementet, første bruker — men se pakkeprisene under: 69 kr/mnd er det reelle gulvet i drift',
    },
    nettbankApi: { nok: 150, note: 'integrert med økonomisystem' },
    oppstart: { nok: 0, note: 'med BankID' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Abonnementet er 0 kr, men det er ikke det samme som gratis bank: uten pakken Banktjenester til 69 kr/mnd betaler du stykkpris per betaling og årsavgift på bedriftskortet. Resten av kostnaden ligger også i stykkpris — per bruker, per varsling, per utenlandskonto og per fullmaktsavtale.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Banktjenester (pakke for nye ENK og AS): 69 kr/mnd — dekker tre nettbankbrukere, 30 KID-utbetalinger og 30 lønnsutbetalinger per mnd, samt årsavgift på bedriftskort Visa Business',
      'Bank og regnskap i ett: 149 kr/mnd — 69 kr Banktjenester + 80 kr DNB Regnskap Mini (valgfritt), der Regnskap Mini inkluderer integrasjon med nettbank',
      'Ekstra bruker: 40–45 kr/mnd',
      'Kodebrikke uten BankID: 200 kr per bruker',
      'Kontoinformasjon fra utenlandsk bank: 50 kr/konto/mnd',
      'Fullmaktsavtale: 100 kr per selskap/mnd',
      'Saldovarsling: 2 kr (SMS) / 4 kr (e-post)',
    ],
    kilde: 'https://www.dnb.no/bedrift/dagligbank/prisliste',
    kildeTekst: 'dnb.no/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'sparebank1-nord-norge',
    navn: 'SpareBank 1 Nord-Norge',
    nettbank: { nok: 130, note: 'basis' },
    nettbankApi: { nok: 175, note: 'med regnskapsintegrasjon' },
    oppstart: { nok: 750, note: 'bestilling/endring av nettbank m/integrasjon' },
    skjulteGebyrer: 'Lav',
    skjulteGebyrerHvorfor:
      'Laveste publiserte månedspris for integrasjon. API-tilgang ligger inne i brukerprisen, og nystartede selskaper får nettbank gratis i 12 måneder.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker: 40 kr/mnd (integrasjonsbruker/API-tilgang inkludert)',
      'Ekstra selskap i konsernnettbank: 90 kr/mnd',
      'Avtale tilknyttet regnskapskontor: 130 kr/mnd',
      'Nystartet AS: nettbank gratis i 12 mnd',
    ],
    kilde: 'https://www.sparebank1.no/nb/nord-norge/bedrift/kundeservice/bestill/prisliste.html',
    kildeTekst: 'sparebank1.no/nord-norge/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'bn-bank',
    navn: 'BN Bank',
    nettbank: { nok: 29, note: 'pakken Enkel, 0 kr for lag/foreninger' },
    nettbankApi: { nok: 89, note: 'pakken Standard, integrasjon inkludert' },
    oppstart: { nok: 500, note: 'etablering av nettbank' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Lav pakkepris, men hver betaling koster 3 kr i Enkel og Standard. Halv transaksjonspris krever pakken Komplett til 249 kr/mnd. Manuelle tjenester ligger på 500–5 000 kr.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Betaling (med/uten melding, KID, lønn): 3 kr — 1,50 kr i Komplett',
      'Pakken Komplett: 249 kr/mnd',
      'Betaling til utlandet: 50 kr',
      'Bedriftskort: 300 kr i årsgebyr + 2,50 kr per transaksjon',
      'Kontobekreftelse: 500 kr. Manuelle tjenester: 500–5 000 kr',
    ],
    kilde: 'https://www.bnbank.no/bedrift/priser/',
    kildeTekst: 'bnbank.no/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'cultura',
    navn: 'Cultura Bank',
    nettbank: { nok: 75, note: 'AS m.fl.; 25 kr for FLI / næringskode 94' },
    nettbankApi: { nok: 275, note: '75 kr nettbank + 200 kr direkte bankintegrasjon' },
    oppstart: { nok: 1500, note: 'etablering av bankintegrasjon (nettbank 0 kr)' },
    skjulteGebyrer: 'Lav',
    skjulteGebyrerHvorfor:
      'Kort og fullstendig publisert prisliste med to brukere inkludert og ingen etableringsgebyr på nettbank. Hovedkostnaden utenfor månedsprisen er engangsgebyret på integrasjonen.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Etablering nettbank: 0 kr (to brukere inkludert)',
      'Ekstra bruker: 50 kr i opprettelse, 0 kr/mnd',
      'Betaling med KID: 2 kr. Uten KID: 3 kr (4 kr via integrasjon)',
      'OCR-giro: 500 kr etablering + 80 kr/mnd per avtale',
      'Mobilbank: 50 kr etablering, 0 kr/mnd',
    ],
    kilde: 'https://www.cultura.no/bedrift/prisliste',
    kildeTekst: 'cultura.no/bedrift/prisliste',
    sjekket: SJEKKET,
  },
  {
    id: 'sparebank1-ostlandet',
    navn: 'SpareBank 1 Østlandet',
    nettbank: { nok: 120, note: 'uten filoverføring' },
    nettbankApi: { nok: 300, note: 'med filoverføring' },
    oppstart: { nok: 500, note: 'tilkobling av økonomisystem' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Integrasjon koster 2,5 × nettbankprisen, og krever oppsettet «med filoverføring». Tilleggsavtaler er gratis, men ekstra brukere og regnskapsbrukere prises hver for seg.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker utover én administrator: 40 kr/mnd',
      'Tilknytning av tilleggsavtaler: 0 kr etablering',
      'SpareBank 1 Regnskap, ekstra bruker: 99 kr/mnd',
      'Lag/foreninger: 0 kr for hovedavtale med filoverføring',
    ],
    kilde: 'https://www.sparebank1.no/nb/ostlandet/bedrift/kundeservice/bestill/prisliste.html',
    kildeTekst: 'sparebank1.no/ostlandet/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'sparebank1-sor-norge',
    navn: 'SpareBank 1 Sør-Norge',
    tidligere: 'tidl. SpareBank 1 SR-Bank',
    nettbank: { nok: 95, note: 'Basis; 0 kr for enkeltpersonforetak' },
    nettbankApi: { nok: 300, note: 'Integrert' },
    oppstart: { nok: 500, note: 'etablering Integrert (Basis 0 kr)' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Tydelige trinn, men integrasjon koster tre ganger Basis, brukerprisen er 50 kr og transaksjonene prises fra 2 til 16 kr avhengig av tjeneste. Filmottak via SFTP koster 5 000 kr å etablere.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker: 50 kr/mnd',
      'Klient-/datteravtale: 60 kr/mnd',
      'SFTP-mottak: 5 000 kr etablering',
      'Transaksjoner: 2–16 kr avhengig av tjeneste',
      'SpareBank 1 Regnskap: 145 / 299 / 499 kr/mnd',
    ],
    kilde: 'https://www.sparebank1.no/nb/sor-norge/bedrift/kundeservice/bestill/prisliste.html',
    kildeTekst: 'sparebank1.no/sor-norge/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'landkreditt',
    navn: 'Landkreditt Bank',
    nettbank: { nok: 200, note: 'én bruker; 100 kr for lag/foreninger og landbrukskunder' },
    nettbankApi: { nok: 300, note: 'med filutveksling' },
    oppstart: { nok: 1500, note: 'nettbank med filutveksling (uten: 0 kr)' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Høyeste nettbankpris blant de rene norske sparebankene, og filutveksling koster 1 500 kr å etablere pluss 5 kr per transaksjon. CREMUL-varianten koster 2 000 kr og 350 kr/mnd.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker: 40 kr/mnd + 50 kr i opprettelse',
      'Filinnsending/-uthenting: 5 kr per transaksjon',
      'CREMUL via Evry: 2 000 kr etablering + 350 kr/mnd',
      'Overgang fra uten til med filutveksling: 1 500 kr',
      'Betaling med KID: 2,25 kr',
    ],
    kilde: 'https://www.landkreditt.no/vare-priser/bank--og-betalingstjenester-for-bedrift/',
    kildeTekst: 'landkreditt.no/vare-priser/bedrift',
    sjekket: SJEKKET,
  },
  {
    id: 'handelsbanken',
    navn: 'Handelsbanken',
    nettbank: { nok: 250, note: 'Nettbedrift, én bruker inkludert' },
    nettbankApi: { nok: 500, note: 'Nettbedrift + filtjenester, faktureres samlet' },
    oppstart: { nok: 1500, note: 'etablering av filtjenester (Nettbedrift 0 kr)' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Filtjenester dobler månedsprisen til 500 kr. sFTP og filkommunikasjon har ingen publisert pris i det hele tatt — du må kontakte Cash Management for tilbud.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Ekstra bruker: 40 kr/mnd',
      'Tilleggsavtale: 100 kr/mnd',
      'Kontoinformasjon fra utenlandsk bank: 150 kr/konto/mnd',
      'sFTP og filkommunikasjon: pris på forespørsel',
    ],
    kilde: 'https://www.handelsbanken.no/no/bedrift/prislister/prisliste-nettbedrift',
    kildeTekst: 'handelsbanken.no/bedrift/prisliste-nettbedrift',
    sjekket: SJEKKET,
  },
  {
    id: 'sparebanken-norge-sor',
    navn: 'Sparebanken Norge (Sør)',
    tidligere: 'tidl. Sparebanken Sør — fusjonert med Sparebanken Vest 2025',
    nettbank: { nok: 95, note: 'basis kundeforhold; Nettbedrift i seg selv 0 kr' },
    nettbankApi: { nok: 445, note: '95 kr basis + 350 kr regnskapsintegrasjon/filtjenester' },
    oppstart: { nok: 0, note: 'SFTP-oppsett hos TietoEVRY: 10 000 kr' },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Nettbedrift er annonsert til 0 kr, men forutsetter «basis kundeforhold» til 95 kr/mnd — en obligatorisk kostnad som ikke framgår av nettbankprisen. Betaling med melding koster 5,50 kr.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Basis kundeforhold (obligatorisk): 95 kr/mnd',
      'Ekstra bruker: 50 kr',
      'Underavtale: 100 kr/mnd uten integrasjon, 150 kr/mnd med',
      'SFTP-kommunikasjon: 10 000 kr etablering',
      'Betaling med KID: 1,75 kr. Med melding: 5,50 kr. Lønn: 2 kr',
    ],
    kilde: 'https://www.sor.no/felles/prisliste/prisliste-bedrift/',
    kildeTekst: 'sor.no/prisliste-bedrift',
    sjekket: SJEKKET,
  },
  {
    id: 'nordea',
    navn: 'Nordea',
    nettbank: { nok: 164, note: '99 kr per selskap + 40 kr bruker + 25 kr konto' },
    nettbankApi: { nok: 264, note: '+ 100 kr Corporate Access Lite (inntil 3 konti)' },
    oppstart: { nok: 320, note: 'etablering per bedrift' },
    skjulteGebyrer: 'Høy',
    skjulteGebyrerHvorfor:
      'Ingen enkelt månedspris finnes — prisen stables av abonnement per selskap, per bruker og per konto. Hver ny konto og hver ny ansatt øker den faste kostnaden. Corporate Access Account Reporting koster 2 500 kr å etablere.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Abonnement per selskap: 99 kr/mnd',
      'Abonnement per bruker: 40 kr/mnd',
      'Abonnement per konto: 25 kr/mnd',
      'Corporate Access Lite (inntil 3 konti): 100 kr/mnd',
      'Corporate Access Account Reporting: 2 500 kr etablering + 75 kr/konto/mnd',
    ],
    kilde: 'https://www.nordea.no/bedrift/priser/Prisliste-kanaler.html',
    kildeTekst: 'nordea.no/bedrift/prislister',
    sjekket: SJEKKET,
  },
  {
    id: 'danske-bank',
    navn: 'Danske Bank',
    nettbank: { nok: 250, note: 'District: kontoopplysninger og onlinetjenester, per landmodul' },
    nettbankApi: { nok: 550, note: 'District med filoverføring, per landmodul' },
    oppstart: { nok: 5000, note: '1 000 kr District + 4 000 kr API-integrasjon' },
    skjulteGebyrer: 'Høy',
    skjulteGebyrerHvorfor:
      'Dyreste oppstart i oversikten: 4 000 kr bare i engangsgebyr for API-integrasjon, og 3 000 kr hvis filoverføring etableres i stedet. Abonnementet prises per landmodul, og rapportmoduler kommer i tillegg.',
    kid: true,
    norskBank: true,
    detaljer: [
      'API-integrasjon, engangsgebyr for etablering: 4 000 kr',
      'Etablering District med filoverføring: 3 000 kr',
      'Ekstra foretak: 1 000 kr etablering + 100 kr/mnd',
      'Statistikk og rapporter: 110 kr/mnd (bedrift) / 330 kr/mnd (konsern)',
      'Cash Flow Forecast: 790 kr/mnd + 250 kr per ekstra bruker',
    ],
    kilde: 'https://danskebank.no/bedrift/kundeservice/priser-og-vilkaar',
    kildeTekst: 'danskebank.no/bedrift/priser',
    sjekket: SJEKKET,
  },
  {
    id: 'sparebanken-norge-vest',
    navn: 'Sparebanken Norge (Vest)',
    tidligere: 'tidl. Sparebanken Vest — fusjonert med Sparebanken Sør 2025',
    nettbank: { nok: null },
    nettbankApi: { nok: null },
    oppstart: { nok: null },
    skjulteGebyrer: 'Middels',
    skjulteGebyrerHvorfor:
      'Nivået er antatt likt Sør-delen av samme bank, som har identisk kategoristruktur i prislisten. Ikke etterprøvd — behandle det som ubekreftet.',
    kid: true,
    norskBank: true,
    detaljer: [
      'Prislisten har egne kategorier for Nettbank bedrift og Basis kundeforhold, men beløpene lastes inn i nettleseren og finnes ikke i sidekilden.',
      'Sør-delen av samme bank: 95 kr/mnd basis + 350 kr/mnd regnskapsintegrasjon.',
    ],
    kilde: 'https://www.spv.no/bedrift/prisliste-bedriftsmarked',
    kildeTekst: 'spv.no/bedrift/priser',
    sjekket: SJEKKET,
    ubekreftet:
      'Prislisten på spv.no rendres i nettleseren, og beløpene lot seg ikke lese ut av sidekilden. Ring 915 05555 (valg 3-1) eller åpne prislisten i nettleser for gjeldende priser.',
  },
  {
    id: 'lunar',
    navn: 'Lunar Business',
    tidligere: 'dansk e-pengeforetak, ikke norsk bank',
    nettbank: { nok: 69, note: 'Solo, for enkeltpersonforetak' },
    nettbankApi: { nok: null, note: 'ingen bankintegrasjon — bare CSV-eksport' },
    oppstart: { nok: 0 },
    skjulteGebyrer: 'Lav',
    skjulteGebyrerHvorfor:
      'Selve prisen er lav og flat, men det finnes ingen integrasjon å kjøpe. Den reelle kostnaden er manuelt arbeid: uten KID/OCR må hver innbetaling matches for hånd.',
    kid: false,
    norskBank: false,
    detaljer: [
      'Ingen KID/OCR-innbetaling og ingen OCR-avtale',
      'Ingen bankintegrasjon mot regnskapssystem — kontoutskrift eksporteres som CSV',
      'Inkluderer Visa-kort, 3 % innskuddsrente inntil 1 mill. kr',
    ],
    kilde: 'https://www.lunar.app/no/bedrift/priser',
    kildeTekst: 'lunar.app/no/bedrift/priser',
    sjekket: SJEKKET,
  },
];

/** Rangeringsvekt for sortering — samme rekkefølge som skalaen leses. */
export const gebyrRangering: Record<SkjulteGebyrer, number> = {
  Ingen: 0,
  Lav: 1,
  Middels: 2,
  Høy: 3,
};

export function formaterKr(pris: Pris): string {
  if (pris.nok === null) return 'Ikke oppgitt';
  if (pris.nok === 0) return '0 kr';
  return `${pris.nok.toLocaleString('nb-NO')} kr`;
}
