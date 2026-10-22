/**
 * Confirming a company through Altinn, and the question asked first when
 * somebody else already has.
 *
 * More than one set of books may be confirmed for an orgnr, but never without
 * the person being told (docs/ARCHITECTURE.md § Confirmed ownership): two
 * confirmed sets can both file for the company. The backend refuses the first
 * attempt with `company_already_claimed`, and only a second one carrying the
 * person's yes goes through. Both pages that confirm come through here, so
 * «Nytt selskap» and «Selskapet» cannot ask the question differently, or
 * forget to.
 */

import { ALREADY_CLAIMED, ApiError, verifyCompany, type Company } from './api';
import { el } from './dom';

/** Put the question to the person; true means «Ja». */
export type AskAnyway = (name: string) => Promise<boolean>;

/**
 * The window in `components/ClaimedDialog.astro`, which every page that
 * confirms carries.
 *
 * Escape and «Avbryt» both close it without a yes, so anything but the «Ja»
 * button is a no.
 */
export function askClaimedAnyway(name: string): Promise<boolean> {
  const dialog = el<HTMLDialogElement>('#likevel-dialog');
  el('#likevel-navn', dialog).textContent = name;
  return new Promise((resolve) => {
    dialog.returnValue = '';
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ja'), { once: true });
    dialog.showModal();
  });
}

/**
 * Confirm `company`, asking first when another account has confirmed its orgnr.
 *
 * Resolves with the confirmed company, or null when the person answered
 * «Avbryt»: the books stay unconfirmed, which is a choice and not a failure.
 * `agreed` is for a page that asked already, where the company was picked, so
 * the same question is not put twice. Every other refusal is thrown as it came.
 */
export async function confirmOwnership(
  company: Company,
  { agreed = false, ask = askClaimedAnyway }: { agreed?: boolean; ask?: AskAnyway } = {},
): Promise<Company | null> {
  if (agreed) return verifyCompany(company.id, true);
  try {
    return await verifyCompany(company.id);
  } catch (error: unknown) {
    if (!(error instanceof ApiError && error.code === ALREADY_CLAIMED)) throw error;
  }
  if (!(await ask(company.name))) return null;
  return verifyCompany(company.id, true);
}
