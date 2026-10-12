/**
 * Rendering the federated sign-in buttons.
 *
 * Which providers exist is decided by the backend, not by this file: it lists
 * only the ones whose credentials are configured, so a button here always
 * leads somewhere. Adding a provider is therefore a backend change and, if its
 * owner offers one for this use, a brand mark in `ProviderButtons.astro`, and
 * nothing else. BankID has no mark on purpose; see that file.
 */

import { getProviders, providerStartUrl, type ProviderIntent } from './api';
import { maybe, setStatus } from './dom';

/** Messages for the markers a failed callback redirects back with. */
export const CALLBACK_ERRORS: Record<string, string> = {
  innlogging_avbrutt: 'Innloggingen ble avbrutt.',
  // Not the same thing as a cancelled login, and the difference matters: this
  // one is our setup, not anything the person did, so it must not tell them to
  // try again. The detail ID-porten sent is in the server log.
  innlogging_ikke_mulig:
    'Innloggingen kunne ikke starte. Det er en feil hos oss, ikke hos deg. ' +
    'Vi har fått beskjed.',
  for_lav_sikkerhet:
    'Innloggingen ga ikke høyt nok sikkerhetsnivå. Logg inn med BankID, ' +
    'Buypass eller Commfides i stedet for MinID.',
  innlogging_utlopt: 'Innloggingen tok for lang tid. Prøv en gang til.',
  innlogging_feilet: 'Vi fikk ikke bekreftet kontoen din hos leverandøren. Prøv en gang til.',
  unverified_provider_email:
    'Det finnes allerede en Myntos-konto med denne e-postadressen, og leverandøren ' +
    'har ikke bekreftet at adressen er din. Logg inn på vanlig måte i stedet.',
  account_disabled: 'Kontoen er deaktivert.',
  // BankID is the only provider that can produce these two today: it has no
  // address to find an account by, so a login with one that is linked to
  // nothing is refused rather than turned into an account.
  no_linked_account:
    'Ingen Myntos-konto er koblet til denne BankID-en ennå. Har du konto, logg inn som ' +
    'vanlig og bytt til BankID under «Din konto». Ellers kan du opprette en konto med BankID.',
  identity_already_linked:
    'Denne innloggingen er allerede koblet til en annen Myntos-konto, eller kontoen har ' +
    'en av samme slag fra før.',
  // The link was started on one account and finished in a browser signed in
  // as another (or as nobody). Refused, because finishing it would let one
  // person's BankID open somebody else's books.
  kobling_annen_konto:
    'Koblingen ble startet fra en annen innlogging enn den du har nå. Start den på nytt ' +
    'fra «Din konto».',
  // An account that signs in with BankID signs in with nothing else, because
  // BankID's terms forbid using it to set up or restore a sign-in of our own.
  // The first is the general refusal, on /konto/ for a Google link; the second
  // is a Google or Microsoft login whose verified address is a BankID account's,
  // read on /logg-inn/ and /registrer/, where the BankID button is.
  bankid_account:
    'Kontoen logger inn med BankID, og bare med BankID. Passord, Google og Microsoft kan ' +
    'ikke brukes på den.',
  email_belongs_to_bankid_account:
    'Kontoen med denne e-postadressen logger inn med BankID. Logg inn med BankID i stedet.',
  // Only a crafted link produces this: the switch to BankID starts from the
  // button on /konto/, which asks for the password first.
  bytte_ikke_bekreftet:
    'Bytte til BankID må startes fra «Din konto», der du bekrefter det med passordet ditt.',
};

function markFor(code: string): SVGElement | null {
  const template = maybe<HTMLTemplateElement>(`template[data-mark="${CSS.escape(code)}"]`);
  const mark = template?.content.firstElementChild?.cloneNode(true);
  return mark instanceof SVGElement ? mark : null;
}

/**
 * Fetch the configured providers and draw a button for each.
 *
 * A failure to reach the API is reported rather than swallowed: silently
 * rendering nothing looks exactly like the feature never having been built,
 * which is the one outcome that would go unnoticed.
 *
 * `intent` is what the page is for: the sign-up page passes `signup`, so a
 * BankID nobody has linked goes on to ask for an address instead of being
 * refused as an unknown login.
 *
 * `hints` puts a line under one provider's button, by code. The sign-up page
 * uses it to say, next to BankID, that an account made with it signs in only
 * with BankID: a consequence somebody should read before pressing, not after.
 */
export async function renderProviderButtons(
  redirectTo: string,
  intent: ProviderIntent = 'login',
  hints: Record<string, string> = {},
): Promise<void> {
  const block = maybe<HTMLElement>('[data-provider-block]');
  const list = maybe<HTMLElement>('[data-provider-buttons]');
  const status = maybe<HTMLElement>('[data-provider-status]');
  if (block === null || list === null) return;

  const template = block.dataset.labelTemplate ?? 'Fortsett med %s';

  let providers;
  try {
    providers = (await getProviders()).providers;
  } catch {
    if (status !== null) {
      setStatus(status, 'Vi fikk ikke hentet innloggingsmetodene. Passord virker fortsatt.');
    }
    return;
  }
  if (providers.length === 0) return;

  list.replaceChildren();
  for (const provider of providers) {
    const link = document.createElement('a');
    link.className = 'btn btn-ghost btn-block provider-btn';
    link.href = providerStartUrl(provider.code, redirectTo, intent);
    const mark = markFor(provider.code);
    if (mark !== null) link.append(mark);
    // textContent, never innerHTML: the label comes from the API.
    link.append(document.createTextNode(template.replace('%s', provider.label)));
    list.append(link);
    const hint = hints[provider.code];
    if (hint !== undefined) {
      const line = document.createElement('p');
      line.className = 'provider-hint';
      line.textContent = hint;
      list.append(line);
    }
  }
  block.hidden = false;
}
