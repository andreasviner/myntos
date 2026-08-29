/**
 * Which company the user is currently looking at.
 *
 * One Myntos account keeps books for as many companies as it likes: a sole
 * trader has one, a regnskapsfører has one per client. Every working page is
 * therefore about *a* company rather than about the account, and they all have
 * to agree on which one. Otherwise the invoice form and the cash-flow register
 * end up on different sets of books without saying so.
 *
 * The answer lives in `localStorage` (see `rememberCompany`) rather than in the
 * URL, because it is a preference and not a location: a link to /faktura/ sent
 * to a colleague should open *their* current company, not the sender's. It is
 * not a permission either. Every request still names the company id, and the
 * backend answers 404 for one the caller cannot reach, so a tampered value
 * gains nothing.
 */

import { listCompanies, type Company } from './api';
import { loadSession, rememberCompany, rememberedCompany, setChromeCompany } from './session';

let pending: Promise<Company[]> | null = null;

/**
 * The companies on the account, fetched once per page load.
 *
 * Shared for the same reason `loadSession` is: the header needs the current
 * company's name, and so does the page under it, and two requests for one
 * answer is one request too many. `force` is for after a company is created or
 * renamed, where the cached list is now behind what the user just did.
 */
export function loadCompanies(force = false): Promise<Company[]> {
  if (pending === null || force) pending = listCompanies();
  return pending;
}

/**
 * Put the current company in the page header, on every page.
 *
 * Gated on the session rather than on the request failing: an anonymous visitor
 * reading /priser/ should not cause a 401 in their console, and the marketing
 * pages have no header to fill.
 */
export function bootstrapHeaderCompany(): void {
  void loadSession()
    .then(async (session) => {
      if (session === null) return;
      // currentCompany fills the header as a side effect, so the answer is
      // needed only for its effect here.
      currentCompany(await loadCompanies());
    })
    // Silent: the header already carries the organization name from the
    // session, which is true, and an error banner about a subtitle would be
    // louder than the thing it describes.
    .catch(() => undefined);
}

/**
 * The company to open a page with: the remembered one, or the first.
 *
 * The choice is stamped back into storage, so the first page load after a
 * signup settles the question for every page that follows.
 */
export function currentCompany(companies: Company[]): Company | undefined {
  const remembered = rememberedCompany();
  // A remembered id can outlive what it points at: a company deleted, or a
  // membership withdrawn. Falling back to the first is what keeps the page
  // usable instead of empty.
  const current = companies.find((company) => company.id === remembered) ?? companies[0];
  // Every page settles on its company here, so this is the one place that knows
  // what the header should say — including on pages that never touch the header
  // themselves. An account with no books yet is left alone rather than blanked:
  // the organization name applyChrome put there is still the truest answer.
  if (current !== undefined) {
    rememberCompany(current.id);
    setChromeCompany(current);
  }
  return current;
}

/** Fill a `<select>` with the companies, and hide it when there is no choice. */
export function fillCompanyPicker(
  picker: HTMLSelectElement,
  companies: Company[],
  currentId: number,
): void {
  picker.replaceChildren();
  for (const company of companies) {
    const option = document.createElement('option');
    option.value = String(company.id);
    option.textContent = company.name;
    option.selected = company.id === currentId;
    picker.append(option);
  }
  // A picker with one option is a control that cannot do anything: worse than
  // no control, because it invites a click that changes nothing.
  picker.hidden = companies.length < 2;
}

/**
 * Wire a company picker.
 *
 * Without `onChange` the page reloads, which is the honest default: a page like
 * /faktura/ holds a draft, a party list and a catalogue that all belong to the
 * company that was showing, and re-deriving them piecemeal is how half of one
 * company's state survives into another's. The choice is remembered *before*
 * the reload, so it is the new company that comes back.
 */
export function mountCompanyPicker(
  picker: HTMLSelectElement,
  companies: Company[],
  current: Company,
  onChange?: (company: Company) => void,
): void {
  fillCompanyPicker(picker, companies, current.id);
  picker.addEventListener('change', () => {
    const chosen = companies.find((company) => company.id === Number(picker.value));
    if (chosen === undefined) return;
    rememberCompany(chosen.id);
    if (onChange === undefined) window.location.reload();
    else onChange(chosen);
  });
}
