/**
 * What a filing that went wrong on the way to Skatteetaten says, on /mva/ and
 * /skattemelding/.
 *
 * Two things decide it, and the pages got neither right until 2026-11-02:
 *
 * - whether the melding was filed at all. A sent MVA-melding whose settlement
 *   against 2740 could not be posted carries an error too, and was told it had
 *   «stoppet underveis» next to a badge saying «Sendt»;
 * - whether Altinn ever made the instance. Without one nothing can have been
 *   sent, because sending is two more calls on that instance, so there is
 *   nothing to look for in Altinn: the person checks again and sends. With one,
 *   the company's inbox on altinn.no is where to look, and the page says so
 *   rather than «sjekk i Altinn», which a person met without knowing where.
 */

export interface FilingOutcome {
  status: string;
  error: string | null;
  altinn_instance_id: string | null;
}

/** The line under the filing, or null when nothing went wrong. `melding` is «meldingen» or «skattemeldingen». */
export function errorText(filing: FilingOutcome, melding: string): string | null {
  if (filing.error === null) return null;
  // Sendt eller mottatt: meldingen står, og feilen gjelder noe etterpå.
  if (filing.status !== 'feilet') return filing.error;
  if (filing.altinn_instance_id === null) {
    return (
      'Ingenting ble sendt: Altinn fikk ikke opprettet innsendingen. ' +
      `Kontroller på nytt og send igjen. Feilen: ${filing.error}`
    );
  }
  return (
    `Innsendingen stoppet underveis. Se i innboksen til selskapet på altinn.no om ${melding} ` +
    `ligger der som sendt, før du prøver igjen. Feilen: ${filing.error}`
  );
}
