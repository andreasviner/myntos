/**
 * Rendering the federated sign-in buttons.
 *
 * Which providers exist is decided by the backend, not by this file: it lists
 * only the ones whose credentials are configured, so a button here always
 * leads somewhere. Adding a fourth provider is therefore a backend change and
 * a brand mark in `ProviderButtons.astro`, and nothing else.
 */

import { getProviders, providerStartUrl } from './api';
import { maybe, setStatus } from './dom';

/** Messages for the markers a failed callback redirects back with. */
export const CALLBACK_ERRORS: Record<string, string> = {
  innlogging_avbrutt: 'Innloggingen ble avbrutt.',
  innlogging_utlopt: 'Innloggingen tok for lang tid. Prøv en gang til.',
  innlogging_feilet: 'Vi fikk ikke bekreftet kontoen din hos leverandøren. Prøv en gang til.',
  unverified_provider_email:
    'Det finnes allerede en Myntos-konto med denne e-postadressen, og leverandøren ' +
    'har ikke bekreftet at adressen er din. Logg inn med passord i stedet.',
  account_disabled: 'Kontoen er deaktivert.',
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
 */
export async function renderProviderButtons(redirectTo: string): Promise<void> {
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
    link.href = providerStartUrl(provider.code, redirectTo);
    const mark = markFor(provider.code);
    if (mark !== null) link.append(mark);
    // textContent, never innerHTML: the label comes from the API.
    link.append(document.createTextNode(template.replace('%s', provider.label)));
    list.append(link);
  }
  block.hidden = false;
}
