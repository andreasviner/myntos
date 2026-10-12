/**
 * How an account signs in, said the way /konto/ says it.
 *
 * The rule behind it is the backend's: an account with a BankID identity signs
 * in with BankID and nothing else, because BankID's terms forbid using a BankID
 * login to set up or restore a sign-in of our own (docs/integrations/bankid.md).
 * The backend keeps the two apart, so a BankID account never also has a
 * password or a Google sign-in, and this file only has to read which kind of
 * account it is. Kept here rather than in the page so it is tested: a page that
 * offered «Koble til Google» to a BankID account would be offering a button the
 * backend refuses.
 */

import type { User } from './api';

type SignInFacts = Pick<User, 'has_password' | 'providers'>;

const LABELS: Record<string, string> = {
  google: 'Google',
  microsoft: 'Microsoft',
  bankid: 'BankID',
};

/** Whether this account signs in with BankID, and so with nothing else. */
export function isBankidAccount(user: SignInFacts): boolean {
  return user.providers.includes('bankid');
}

function joinedWithOr(names: string[]): string {
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} eller ${names[names.length - 1]}`;
}

/**
 * One sentence: «Du logger inn med …».
 *
 * BankID is named alone even if something else were somehow left beside it,
 * because the backend no longer lets anything else in.
 */
export function describeSignIn(user: SignInFacts): string {
  if (isBankidAccount(user)) return 'Du logger inn med BankID.';
  const ways = [
    ...(user.has_password ? ['e-post og passord'] : []),
    ...user.providers.map((code) => LABELS[code] ?? code),
  ];
  if (ways.length === 0) return 'Kontoen har ingen innlogging.';
  return `Du logger inn med ${joinedWithOr(ways)}.`;
}
