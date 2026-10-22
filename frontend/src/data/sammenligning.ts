/**
 * Sammenligninger: Myntos side om side med et annet regnskapsprogram.
 *
 * For den som har søkt etter et alternativ til programmet de har, og vil vite
 * hva de bytter til og hva de mister. Sidene står eller faller på at de er
 * riktige, så hver pris er hentet fra programmets egen prisside på datoen i
 * `SJEKKET`, med kilden nederst på siden, og alt Myntos ikke gjør ennå står like
 * tydelig som det Myntos gjør. En sammenligning som bare viser fordelene, leses
 * som reklame av folk og gjengis feil av KI-assistenter.
 *
 * Det Myntos ikke gjør, er det samme som `notYet` på /funksjoner/. Blir noe av
 * det bygget, endres det her også, ellers sier de to sidene forskjellige ting.
 *
 * Konkurrentenes priser endrer seg. Kontroller dem mot kildene og flytt
 * `SJEKKET` når de er sett over; en side med en gammel dato sier selv hvor
 * gammel den er. Bygget fra docs/FEATURES.md, «Price points», og prissidene.
 */

/** ISO-dato for når prisene sist ble kontrollert mot programmenes egne prissider. */
export const SJEKKET = '2026-10-22';

export interface Kilde {
  tekst: string;
  href: string;
}

/** Én rad i pristabellen: hva det gjelder, hva Myntos tar, hva det andre programmet tar. */
export interface Rad {
  hva: string;
  myntos: string;
  dem: string;
}

export interface Sammenligning {
  /** URL-biten: /sammenlign/fiken/ */
  slug: string;
  /** Programmets navn, brukt i løpende tekst. */
  navn: string;
  /** Sidens <title>, uten « | Myntos». */
  tittel: string;
  /** Meta description, høyst 170 tegn (pnpm check:seo). */
  beskrivelse: string;
  ingress: string;
  /** Én linje på oversiktssiden. */
  kortTekst: string;
  priser: Rad[];
  /** Hva et lite aksjeselskap betaler i året, regnet ut fra radene over. */
  regnestykke: { dem: string; myntos: string };
  /** Det programmet har som Myntos ikke har ennå. */
  demForan: string[];
  /** Det som er annerledes i Myntos, utover det som gjelder alle sammenligningene. */
  myntosAnnerledes: string[];
  /** Når Myntos passer. */
  velgMyntos: string[];
  /** Når det andre programmet passer bedre. */
  velgDem: string[];
  /** Flytteguiden for programmet, under /kom-i-gang/. */
  flytteguide: string;
  kilder: Kilde[];
}

/** Det som skiller Myntos fra alle programmene her, sagt én gang. */
export const myntosFelles: string[] = [
  'Gratis for alltid, også skattemeldingen for aksjeselskap og alt som kommer senere',
  'Offentlig kildekode: du kan lese hvordan tallene blir til, og drifte Myntos selv',
  'KI-en som leser bilagene, kjører i Norge, lagrer ingenting og kan slås av for hele selskapet',
  'Hele API-et uten tillegg i prisen, også for ChatGPT, Claude eller en modell du kjører selv',
];

/** Lovkildene sidene bygger på, felles for alle sammenligningene. */
export const lovkilder: Kilde[] = [
  {
    tekst: 'Skatteetaten: aksjonærregisteroppgaven sendes fra et sluttbrukersystem fra juni 2026',
    href: 'https://www.skatteetaten.no/en/forms/aksjonarregisteroppgaven',
  },
  {
    tekst: 'Revisorforeningen: e-faktura mellom bedrifter fra 1. januar 2027',
    href: 'https://www.revisorforeningen.no/fag/nyheter/e-faktura-fra-2027-alle-ma-kunne-sende--ikke-alle-er-like-godt-forberedt/',
  },
];

export const sammenligninger: Sammenligning[] = [
  {
    slug: 'fiken',
    navn: 'Fiken',
    tittel: 'Myntos eller Fiken? Pris og funksjoner sammenlignet',
    beskrivelse:
      'Myntos og Fiken side om side: hva det koster i året, hva Fiken har som Myntos ikke har ennå, og hva som er annerledes. Prisene er fra Fikens egen prisside.',
    ingress:
      'Fiken og Myntos er laget for de samme selskapene: små bedrifter som fører regnskapet selv. Her er forskjellene, med prisene fra Fikens egen prisside og det Myntos ikke gjør ennå, så du kan velge med åpne øyne.',
    kortTekst: 'Ett abonnement til 219 kr i måneden, med tillegg for bank, lønn og årsoppgjør.',
    priser: [
      { hva: 'Abonnement', myntos: '0 kr, for alltid', dem: '219 kr/mnd, ingen bindingstid' },
      { hva: 'Prøveperiode', myntos: 'Trengs ikke', dem: '30 dager gratis' },
      { hva: 'Brukere', myntos: 'Så mange du vil, 0 kr', dem: 'Så mange du vil, 0 kr' },
      {
        hva: 'Skattemelding for aksjeselskap',
        myntos: '0 kr',
        dem: '1 590 kr/år, med årsregnskap og aksjonærregisteroppgave',
      },
      { hva: 'Skattemelding for enkeltpersonforetak', myntos: 'Ikke i Myntos ennå', dem: '1 290 kr/år' },
      { hva: 'Bank', myntos: 'Kontoutskrift som fil (camt.053), 0 kr', dem: 'Bankkobling, 69 kr/mnd' },
      { hva: 'KID på fakturaen', myntos: '0 kr', dem: '69 kr/mnd' },
      { hva: 'Sende EHF', myntos: 'EHF-filen lages, men sendes ikke ennå', dem: 'Inkludert' },
      { hva: 'Lønn', myntos: 'Ikke i Myntos ennå', dem: '69 kr per bruker/mnd' },
      { hva: 'API og egne integrasjoner', myntos: '0 kr, også for KI-assistenter', dem: '99 kr/mnd' },
    ],
    regnestykke: {
      dem: 'Et aksjeselskap uten ansatte som leverer skattemeldingen fra Fiken, betaler 4 218 kr i året (219 kr × 12 + 1 590 kr), og 5 046 kr med bankkobling. Alle Fikens priser er uten mva.',
      myntos:
        'I Myntos koster det samme 0 kr. Men Fikens pakke for aksjeselskap leverer også årsregnskapet og aksjonærregisteroppgaven, og det gjør ikke Myntos ennå.',
    },
    demForan: [
      'Lønn og a-melding',
      'Årsregnskapet til Regnskapsregisteret, og aksjonærregisteroppgaven, som siden juni 2026 bare kan sendes fra et regnskapsprogram',
      'Skattemelding med næringsspesifikasjon for enkeltpersonforetak',
      'Sending av EHF, som blir påbudt fra 1. januar 2027 for fakturaer til bedrifter som kan ta imot e-faktura',
      'Bankkobling som henter transaksjonene selv',
      'Å registrere at en kunde har betalt fakturaen',
      'Prosjektregnskap, timeføring og reiseregning',
    ],
    myntosAnnerledes: [],
    velgMyntos: [
      'du driver et aksjeselskap uten ansatte, eller kjører lønnen i et annet program',
      'du vil levere skattemeldingen for aksjeselskapet uten å betale for det',
      'du vil kunne lese koden som fører regnskapet ditt, eller drifte det selv',
      'du vil bruke din egen KI-assistent i regnskapet uten et tillegg i prisen',
    ],
    velgDem: [
      'du har ansatte eller tar ut lønn',
      'du fakturerer bedrifter som tar imot e-faktura: fra 1. januar 2027 skal de ha fakturaen som EHF, og Myntos sender den ikke ennå',
      'du vil levere årsregnskapet og aksjonærregisteroppgaven fra samme program som regnskapet',
      'du har enkeltpersonforetak og vil levere næringsspesifikasjonen fra programmet',
      'du vil at banken skal være koblet til programmet',
    ],
    flytteguide: '/kom-i-gang/fiken/',
    kilder: [{ tekst: 'Fiken: priser', href: 'https://fiken.no/priser' }],
  },
  {
    slug: 'tripletex',
    navn: 'Tripletex',
    tittel: 'Myntos eller Tripletex? Pris og funksjoner sammenlignet',
    beskrivelse:
      'Myntos og Tripletex side om side: pakker, bilagsgrense, årsoppgjør og transaksjonspriser, hva Tripletex har som Myntos ikke har ennå, og hva som er annerledes.',
    ingress:
      'Tripletex har pakker med lønn, prosjekt, lager og egne bransjeløsninger. Myntos er laget for den lille bedriften som fører regnskapet selv. Her er forskjellene, med prisene fra Tripletex sine egne sider og det Myntos ikke gjør ennå.',
    kortTekst: 'Pakker fra 199 til 649 kr i måneden, 500 bilag i året, og pris per transaksjon.',
    priser: [
      {
        hva: 'Abonnement',
        myntos: '0 kr, for alltid',
        dem: 'Basis fra 199 kr/mnd, Smart fra 299, Pro fra 479, Komplett fra 649',
      },
      {
        hva: 'Bilag',
        myntos: 'Så mange du vil',
        dem: '500 i året inkludert, deretter fra 210 kr/mnd',
      },
      {
        hva: 'Skattemelding for aksjeselskap',
        myntos: '0 kr',
        dem: 'Årsoppgjøret, 990 kr/år, med årsregnskap og aksjonærregisteroppgave',
      },
      {
        hva: 'Skattemelding for enkeltpersonforetak',
        myntos: 'Ikke i Myntos ennå',
        dem: 'Med i årsoppgjøret, 990 kr/år',
      },
      {
        hva: 'Bank',
        myntos: 'Kontoutskrift som fil (camt.053), 0 kr',
        dem: 'Bankintegrasjon 49 kr/mnd, og 4 kr per betaling fra programmet',
      },
      { hva: 'Innbetaling med KID', myntos: 'KID på fakturaen, 0 kr', dem: '4 kr per innbetaling' },
      { hva: 'Sende EHF', myntos: 'EHF-filen lages, men sendes ikke ennå', dem: '4 kr per EHF, sendt eller mottatt' },
      {
        hva: 'Lønn',
        myntos: 'Ikke i Myntos ennå',
        dem: 'Én bruker med i Pro og Komplett, ellers 65 kr per bruker/mnd',
      },
      {
        hva: 'Integrasjoner',
        myntos: '0 kr, også for KI-assistenter',
        dem: '129 kr/mnd, med i Pro og Komplett',
      },
    ],
    regnestykke: {
      dem: 'Et aksjeselskap uten ansatte på Basis, med årsoppgjøret, betaler 3 378 kr i året (199 kr × 12 + 990 kr), og 3 966 kr med bankintegrasjon, før transaksjonspriser og bilag utover 500. Alle Tripletex sine priser er uten mva.',
      myntos:
        'I Myntos koster det samme 0 kr. Men årsoppgjøret i Tripletex leverer også årsregnskapet og aksjonærregisteroppgaven, og det gjør ikke Myntos ennå.',
    },
    demForan: [
      'Lønn, a-melding og reiseregning',
      'Årsregnskapet til Regnskapsregisteret, og aksjonærregisteroppgaven, som siden juni 2026 bare kan sendes fra et regnskapsprogram',
      'Skattemelding med næringsspesifikasjon for enkeltpersonforetak',
      'Sending og mottak av EHF; sendingen blir påbudt fra 1. januar 2027 for fakturaer til bedrifter som kan ta imot e-faktura',
      'Bankintegrasjon, med betaling av regninger fra programmet',
      'Å registrere at en kunde har betalt fakturaen',
      'Prosjekt, timeregistrering, lager og attestering',
    ],
    myntosAnnerledes: ['Ingen grense for antall bilag, og ingen pris per transaksjon'],
    velgMyntos: [
      'du driver et aksjeselskap uten ansatte, eller kjører lønnen i et annet program',
      'du vil levere skattemeldingen for aksjeselskapet uten å betale for årsoppgjøret',
      'du vil slippe å følge med på bilagsgrenser og transaksjonspriser',
      'du vil kunne lese koden som fører regnskapet ditt, eller drifte det selv',
    ],
    velgDem: [
      'du har ansatte eller tar ut lønn',
      'du fakturerer bedrifter som tar imot e-faktura: fra 1. januar 2027 skal de ha fakturaen som EHF, og Myntos sender den ikke ennå',
      'du vil levere årsregnskapet og aksjonærregisteroppgaven fra samme program som regnskapet',
      'du fører prosjekter, timer eller lager',
      'du vil betale regningene rett fra regnskapsprogrammet',
    ],
    flytteguide: '/kom-i-gang/tripletex/',
    kilder: [
      { tekst: 'Tripletex: priser', href: 'https://www.tripletex.no/priser/' },
      { tekst: 'Tripletex: transaksjonspriser', href: 'https://www.tripletex.no/transaksjonspriser/' },
      { tekst: 'Tripletex: årsoppgjør', href: 'https://www.tripletex.no/arsoppgjor/' },
    ],
  },
];
