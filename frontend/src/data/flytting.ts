/**
 * Flytteguider: hvordan du får regnskapet ut av et annet norsk
 * regnskapsprogram, og hva du gjør med det når du er i Myntos.
 *
 * Menyveiene under er hentet fra leverandørenes egne hjelpesider på datoen i
 * `sjekket`, og `kilder` peker alltid tilbake dit. Menyer flyttes uten
 * forvarsel. Derfor står kilden ved siden av hvert steg framfor å bli gjemt i
 * en fotnote, og derfor beskriver stegene *hva* du leter etter og ikke bare
 * hvor det lå sist vi så etter.
 *
 * Rekkefølgen i `guider` er rekkefølgen i menyen og på oversiktssiden.
 */

/** ISO-dato for når menyveiene sist ble kontrollert mot leverandørens hjelpeside. */
export const SJEKKET = '2026-08-13';

export interface Kilde {
  tekst: string;
  href: string;
}

export interface Steg {
  tittel: string;
  tekst: string;
  /** Menyveien i det gamle programmet, vist som en egen sti under teksten. */
  vei?: string;
}

export interface Guide {
  /** URL-biten: /kom-i-gang/fiken/ */
  slug: string;
  /** Programmets navn, brukt i løpende tekst. */
  navn: string;
  /** Tittelen i menyen og på kortet. */
  menyTittel: string;
  /** Én linje i menyen: hva som er det særegne ved akkurat dette programmet. */
  menyTekst: string;
  ingress: string;
  /** Slik henter du dataene ut av det gamle programmet. */
  steg: Steg[];
  /** Filer du skal ha lokalt før du sier opp abonnementet. */
  taVarePa: string[];
  /** Fellene som er spesifikke for dette programmet. */
  verdtAVite: string[];
  kilder: Kilde[];
}

export const guider: Guide[] = [
  {
    slug: 'fiken',
    navn: 'Fiken',
    menyTittel: 'Flytte fra Fiken',
    menyTekst: 'Komplett eksport, bilagsarkiv og SAF-T per regnskapsår.',
    ingress:
      'Fiken samler alt på én side, og gir deg både SAF-T-filen og en komplett kopi av bilagsarkivet. Ta begge deler: SAF-T er tallene, arkivet er dokumentasjonen bak dem.',
    steg: [
      {
        tittel: 'Åpne eksportsiden',
        tekst:
          'Alle eksportene i Fiken ligger samlet under Foretak. Du trenger ikke å avslutte noe for å laste ned, og eksporten kan kjøres så mange ganger du vil.',
        vei: 'Foretak → Eksport av regnskap',
      },
      {
        tittel: 'Last ned SAF-T-eksporten',
        tekst:
          'Fiken lager SAF-T per regnskapsår. Last ned filen for hvert år du har ført i Fiken, ikke bare det siste. Det er hele historikken Skatteetaten kan be om, ikke inneværende år.',
      },
      {
        tittel: 'Last ned komplett eksport',
        tekst:
          'Den komplette eksporten er en ZIP med alle opplastede dokumenter, fakturaer, ordrer, tilbud og kreditnotaer, pluss Excel-oversikter per regnskapsår. Dette er kopien du faktisk kan slå opp i senere.',
      },
      {
        tittel: 'Har du kjørt lønn, ta lønnsslippene også',
        tekst:
          'Lønnsslipper eksporteres for seg. De følger ikke med i SAF-T Regnskap, og de er ikke enkle å rekonstruere i etterkant.',
      },
    ],
    taVarePa: [
      'SAF-T-fil for hvert regnskapsår du har ført i Fiken',
      'Komplett eksport (ZIP) med bilag, fakturaer og Excel-oversikter',
      'Lønnsslipper, hvis du har kjørt lønn',
    ],
    verdtAVite: [
      'Starter du i et nytt program 1. januar, er det SAF-T-filen til og med 31. desember året før som er inngangen. Fiken stiller samme krav motsatt vei: filen må være datert dagen før oppstart hos dem.',
      'Har du kjørt lønn i år, fullfør lønnsåret og send a-meldingene fra Fiken før du flytter. Lønn er det eneste som virkelig er vondt å bytte midt i.',
      'Last ned alt før du sier opp abonnementet. Tilgangen til eksportsiden følger abonnementet, ikke oppbevaringsplikten din.',
    ],
    kilder: [
      {
        tekst: 'Fiken: Eksport av regnskap fra Fiken',
        href: 'https://hjelp.fiken.no/eksport-av-regnskap-fra-fiken',
      },
      {
        tekst: 'Fiken: Inngående balanse med SAF-T-fil',
        href: 'https://hjelp.fiken.no/inngaaende-balanse-med-saf-t-fil-overgang-til-fiken',
      },
    ],
  },
  {
    slug: 'tripletex',
    navn: 'Tripletex',
    menyTittel: 'Flytte fra Tripletex',
    menyTekst: 'Regnskapseksport (SAF-T), bilagene med vedlegg som PDF, og lister som CSV.',
    ingress:
      'Tripletex legger SAF-T-eksporten under kontoplanen framfor under innstillinger, noe som er lett å lete forbi. SAF-T-filen har regnskapet, men ikke kvitteringene og fakturaene bak bilagene: Tripletex har ingen samlet eksport av dem, men skriver dem ut som PDF fra bilagsoversikten, og den utskriften leser Myntos.',
    steg: [
      {
        tittel: 'Finn regnskapseksporten',
        tekst:
          'Eksporten ligger på kontoplanen, ikke i innstillingene. Er du innlogget som regnskapsfører, må du stå i riktig klient først.',
        vei: 'Regnskap → Kontoplan → Regnskapseksport (SAF-T)',
      },
      {
        tittel: 'Velg periode og last ned',
        tekst:
          'Oppgi fra- og til-dato for perioden du vil ha ut, og velg Last ned regnskap. Uten avkrysning for dokumentmottak havner filen rett i nedlastingsmappen din.',
      },
      {
        tittel: 'Vent på ZIP-en',
        tekst:
          'Et år med mange bilag tar tid å pakke. Lukker du fanen for tidlig, må du starte eksporten på nytt, for det er ingen kø som fortsetter i bakgrunnen.',
      },
      {
        tittel: 'Skriv ut bilagene med vedlegg',
        tekst:
          'Velg perioden i bilagsoversikten, huk av i kolonnen «Dato» så alle bilagene er valgt, og velg «Skriv ut valgte bilag». Last ned det som vises, som PDF. Hvert bilag kommer med Tripletex-siden først og vedleggene etter. Er det mye, ta en måned om gangen, som Tripletex selv anbefaler.',
        vei: 'Bilag → Bilagsoversikt → Skriv ut valgte bilag',
      },
      {
        tittel: 'Ta med listene',
        tekst:
          'Kunde- og leverandørlistene eksporteres for seg som CSV, og er de enkleste å legge inn igjen manuelt i et nytt system.',
      },
    ],
    taVarePa: [
      'SAF-T-eksport (ZIP) for hvert regnskapsår i Tripletex',
      'Utskriften av bilagene med vedlegg, som PDF, for hvert år',
      'Kunde- og leverandørliste som CSV',
      'Egne rapporter du er vant til å se på, som PDF. De finnes ikke i SAF-T',
    ],
    verdtAVite: [
      'Eksporten speiler regnskapet i det øyeblikket du kjører den. Fører du noe etterpå, stemmer ikke filen lenger, og du må kjøre den på nytt.',
      'Har du timer, prosjekt eller lønn i Tripletex, ligger det utenfor SAF-T Regnskap. Eksporter det du trenger fra de modulene separat.',
      'Last opp utskriften av bilagene under «Hent regnskapet fra et annet program», med Tripletex valgt som programmet du kommer fra, etter at SAF-T-filene er hentet. Myntos legger hvert bilags sider på bilaget med samme nummer i bilagsarkivet.',
      'Tripletex tilbyr lesetilgang i fem år etter at du har sagt opp, hvis du vil ha originalene tilgjengelige der også.',
    ],
    kilder: [
      {
        tekst: 'Tripletex: Slik eksporterer du regnskapet ditt i SAF-T-format',
        href: 'https://hjelp.tripletex.no/hc/no/articles/4406369731729-Slik-eksporterer-du-regnskapet-ditt-i-SAF-T-format',
      },
      {
        tekst: 'Tripletex: Hvordan laster jeg ned regnskapsdataene mine?',
        href: 'https://hjelp.tripletex.no/hc/no/articles/4406369645073-Hvordan-laster-jeg-ned-regnskapsdataene-mine',
      },
      {
        tekst: 'Tripletex: Hvordan eksporterer jeg kundelisten?',
        href: 'https://hjelp.tripletex.no/hc/no/articles/22608312821137-Hvordan-eksporterer-jeg-kundelisten',
      },
    ],
  },
  {
    slug: 'poweroffice-go',
    navn: 'PowerOffice Go',
    menyTittel: 'Flytte fra PowerOffice Go',
    menyTekst: 'Eksportkø med versjonsvalg: v1.30 eller v1.40.',
    ingress:
      'PowerOffice Go kjører eksporten som en jobb du bestiller og henter når den er ferdig. Til gjengjeld får du velge SAF-T-versjon selv, noe de færreste programmene lar deg gjøre.',
    steg: [
      {
        tittel: 'Åpne eksportlisten',
        tekst: 'Eksport ligger under kategorien Regnskap i innstillingene.',
        vei: 'Innstillinger → Regnskap → Eksport',
      },
      {
        tittel: 'Bestill en ny eksport',
        tekst:
          'Velg Ny eksport, sett eksporttype til SAF-T Regnskap, fyll inn beskrivelsen (den er påkrevd) og oppgi perioden. Velg v1.30 for perioder til og med 2026; v1.40 er den som gjelder fra 1. januar 2027.',
      },
      {
        tittel: 'Hent filen når jobben er fullført',
        tekst:
          'Eksporten kjøres i bakgrunnen. Du laster ned ZIP-en fra lenken i meldingsboksen, eller fra statusen Fullført i eksportlisten.',
      },
      {
        tittel: 'Eksporter bilags-PDF-ene',
        tekst:
          'Bilagsdokumentene hentes som egen eksport. Det er dem du trenger ved et bokettersyn. Selve SAF-T-filen er tallene, ikke kvitteringene.',
      },
    ],
    taVarePa: [
      'SAF-T Regnskap (ZIP) for hver periode du har ført',
      'Eksport av bilags-PDF-er',
      'Lønnsslipper og lønnsrapporter, hvis lønn er kjørt i Go',
    ],
    verdtAVite: [
      'Eksporten krever at det er satt en kontaktperson i firmainnstillingene på klienten. Mangler den, feiler jobben uten at det er åpenbart hvorfor.',
      'Er du klient hos en regnskapsfører, er det de som har tilgangen til eksporten. Dataene er dine, så be om filene skriftlig, så har du det dokumentert.',
    ],
    kilder: [
      {
        tekst: 'PowerOffice: Eksport av regnskap og lønn i PowerOffice Go',
        href: 'https://hjelpesenter.poweroffice.no/eksport-regnskap-loen',
      },
      {
        tekst: 'PowerOffice: Eksport av SAF-T Regnskap (utvidet informasjon)',
        href: 'https://support.poweroffice.com/hc/no/articles/360035324351-Eksport-av-SAF-T-Regnskap-utvidet-informasjon',
      },
    ],
  },
  {
    slug: 'conta',
    navn: 'Conta',
    menyTittel: 'Flytte fra Conta',
    menyTekst: 'SAF-T ligger under rapportene, ved siden av Skatteetaten.',
    ingress:
      'I Conta ligger SAF-T-eksporten der den logisk hører hjemme: under rapportene, i seksjonen for det som skal til Skatteetaten. Eksporten er en ZIP, og den låser ikke perioden. Det må du gjøre selv.',
    steg: [
      {
        tittel: 'Gå til regnskapsrapportene',
        tekst: 'Eksporten ligger sammen med det andre som sendes til Skatteetaten.',
        vei: 'Regnskapsrapporter → Til Skatteetaten → Eksporter regnskapsdata (SAF-T)',
      },
      {
        tittel: 'Velg perioden du vil ha ut',
        tekst:
          'Ta hele regnskapsår framfor måneder. Et halvt år er sjelden det Skatteetaten spør etter, og det er tyngre å sette sammen igjen senere.',
      },
      {
        tittel: 'Last ned ZIP-en og la perioden stå i fred',
        tekst:
          'Fører du noe i den eksporterte perioden etterpå, stemmer ikke filen lenger med regnskapet. Avslutt perioden først, eksporter etterpå.',
      },
      {
        tittel: 'Last ned bilag og vedlegg',
        tekst:
          'Bilagene du har lastet opp i Conta må lastes ned separat. Oppbevaringsplikten gjelder dokumentasjonen, ikke bare tallene.',
      },
    ],
    taVarePa: [
      'SAF-T-eksport (ZIP) per regnskapsår',
      'Opplastede bilag og vedlegg',
      'Fakturaer du har sendt fra Conta, som PDF',
    ],
    verdtAVite: [
      'Bruker du Safari, pakkes ZIP-en ut automatisk ved nedlasting. Skru av «Åpne sikre filer etter nedlasting» under Innstillinger → Generelt, ellers mister du filen slik den ble levert.',
      'Har du startet lønnsåret i Conta, fullfør og send a-meldingen derfra før du flytter.',
    ],
    kilder: [
      {
        tekst: 'Conta: Eksportere data fra regnskapet (SAF-T)',
        href: 'https://hjelp.conta.no/regnskap/rapporter-regnskap/eksportere-data-fra-regnskapet-saf-t/',
      },
    ],
  },
  {
    slug: 'annet-program',
    navn: 'et annet program',
    menyTittel: 'Flytte fra et annet program',
    menyTekst: 'DNB Regnskap, SpareBank 1, Visma, Luca og resten.',
    ingress:
      'Alle norske regnskapssystemer må kunne levere SAF-T Regnskap. Det er et krav fra Skatteetaten, ikke en funksjon leverandøren velger. Så selv om menyen ser annerledes ut enn i guidene over, finnes filen.',
    steg: [
      {
        tittel: 'Let etter ordet SAF-T',
        tekst:
          'Den ligger nesten alltid ett av tre steder: under innstillinger («Eksport»), under rapporter («Til Skatteetaten»), eller på kontoplanen. Søkefeltet i hjelpesenteret til leverandøren er raskere enn å lete i menyen.',
      },
      {
        tittel: 'Velg riktig versjon hvis du blir spurt',
        tekst:
          'v1.30 gjelder for perioder til og med 2026. v1.40 er obligatorisk for perioder fra 1. januar 2027. Blir du ikke spurt, velger programmet for deg.',
      },
      {
        tittel: 'Last ned bilagsarkivet i tillegg',
        tekst:
          'SAF-T inneholder posteringene, ikke nødvendigvis kvitteringene. Se etter en egen eksport av bilag, vedlegg eller dokumenter.',
      },
      {
        tittel: 'Får du den ikke ut, be om den skriftlig',
        tekst:
          'Regnskapsdataene er dine. Finner du ingen eksport i programmet, be leverandøren eller regnskapsføreren om SAF-T-filen på e-post, så har du både filen og dokumentasjon på at du ba om den.',
      },
    ],
    taVarePa: [
      'SAF-T Regnskap for hvert regnskapsår',
      'Bilag, vedlegg og utgående fakturaer som PDF',
      'Kunde- og leverandørliste, gjerne som CSV',
      'Lønnsslipper og innsendte a-meldinger, hvis du har kjørt lønn',
    ],
    verdtAVite: [
      'Kommer du fra DNB Regnskap eller SpareBank 1 Regnskap, er begge bygget på Unimicro og har samme eksportmeny.',
      'Kommer du fra et skrivebordsprogram eller et regneark, finnes det ingen SAF-T. Da er saldobalansen per siste avsluttede år, og bilagsmappen, det du trenger.',
    ],
    kilder: [
      {
        tekst: 'Skatteetaten: SAF-T Regnskap (dokumentasjon på GitHub)',
        href: 'https://github.com/Skatteetaten/saf-t',
      },
      {
        tekst: 'DNB Regnskap: SAF-T-eksport',
        href: 'https://hjelp.dnbregnskap.dnb.no/no/article/saf-t-eksport-xit0ak/',
      },
    ],
  },
];

export const guideEtterSlug = new Map(guider.map((guide) => [guide.slug, guide]));

/**
 * Det som gjelder uansett hvor du kommer fra. Rekkefølgen er ikke tilfeldig:
 * lønn må avsluttes før perioden, perioden før eksporten, eksporten før
 * oppsigelsen.
 */
export const sjekkliste: Steg[] = [
  {
    tittel: 'Flytt ved et periodeskille',
    tekst:
      'Et årsskifte er best. Midt i et år henter Myntos årets bilag fra SAF-T-filen, og da er skillet mellom to mva-terminer det enkleste: terminene før sender det gamle programmet, resten sender du fra Myntos.',
  },
  {
    tittel: 'Fullfør lønnsåret i det gamle programmet',
    tekst:
      'Har du kjørt lønn i år, kjør den ferdig og send a-meldingene derfra. A-meldingen er kumulativ gjennom året, og et halvt lønnsår i hvert system er ekte arbeid å rydde opp i.',
  },
  {
    tittel: 'Avslutt perioden før du eksporterer',
    tekst:
      'En SAF-T-fil er et øyeblikksbilde. Fører du noe i perioden etter eksporten, må filen lages på nytt, ellers har du to versjoner av samme år.',
  },
  {
    tittel: 'Last ned alt før du sier opp',
    tekst:
      'Oppbevaringsplikten er som hovedregel fem år etter regnskapsårets slutt, og den følger deg, ikke abonnementet ditt. Når abonnementet er sagt opp, er ikke lenger nedlastingsknappen ditt problem å nå, men ditt problem å ha mistet.',
  },
  {
    tittel: 'Lagre kopien et sted du finner den igjen',
    tekst:
      'Filene fra det gamle programmet er dokumentasjonen din helt til de fem årene er ute. En mappe i skyen med to-faktor er nok; nedlastingsmappen på en laptop er det ikke.',
  },
];

/**
 * Hva som faktisk skjer i Myntos etterpå. Dette er dagens sannhet, ikke
 * planen: SAF-T-filene leses inn, tidligere år og året man står i, mens
 * dokumentene bak bilagene ikke gjør det, og en flytteguide som later som noe
 * annet er verdiløs akkurat i det øyeblikket noen prøver å følge den.
 */
export const startISteg: Steg[] = [
  {
    tittel: 'Opprett konto og selskap',
    tekst:
      'Registrer deg, og legg inn selskapet med organisasjonsnummer. Kontoplan med næringsspesifikasjon, mva-koder og regnskapsår settes opp automatisk.',
  },
  {
    tittel: 'Last opp SAF-T-filene under «Hent regnskapet fra et annet program»',
    tekst:
      'Én fil per regnskapsår, så langt tilbake du vil ha regnskapet i Myntos, og filen for året du står i fram til i dag. Myntos leser dem på serveren, så du kan lukke siden mens det pågår, og viser hva som blir ført: inngående balanse fra det første året, bilagene for hvert år, og kontoene, kundene og leverandørene som legges til.',
  },
  {
    tittel: 'Se over og før',
    tekst:
      'Alt føres i én operasjon når du sier ja, med bilagsnumrene fra det gamle programmet. Bilagene ser du under Bilag. Fører du i begge programmene en stund, sammenligner du dem på samme side med en ny fil så ofte du vil. Tidligere år som er ferdige, avslutter du under Årsavslutning.',
  },
  {
    tittel: 'Legg inn nye kunder og leverandører når de kommer',
    tekst:
      'De som står i SAF-T-filene, kom med derfra. Nye legger du inn når du handler med dem. Hver part får en balansekonto, slik SAF-T krever.',
  },
  {
    tittel: 'Før fra og med datoen du flyttet',
    tekst:
      'Registrer inn- og utbetalinger med type og PDF. Dokumentasjonen for bilagene som ble hentet, ligger i bilagsarkivet til det gamle programmet: ta vare på det sammen med SAF-T-filene. Kommer du fra Tripletex, kan du også legge den inn i Myntos fra utskriften av bilagene.',
  },
];

/** Statusen på import, skrevet én gang og gjenbrukt på alle sidene. */
export const importStatus = {
  tittel: 'Myntos henter regnskapet fra SAF-T-filene, også tidligere år',
  tekst:
    'Last opp SAF-T-filene under «Hent regnskapet fra et annet program», én per regnskapsår. Myntos henter kontoene, kundene og leverandørene, inngående balanse fra det første året og bilagene for hvert år, og året du står i fram til datoen filen ble tatt ut. Det som ikke er i SAF-T, kommer ikke med av seg selv: dokumentene bak bilagene (PDF-ene), lønn, timer og prosjekter. Fra Tripletex kan bilagene med vedlegg lastes opp som PDF etterpå, og Myntos legger dem på riktig bilag. Derfor handler guidene her også om å få dataene trygt ut av det gamle programmet før du sier det opp.',
};
