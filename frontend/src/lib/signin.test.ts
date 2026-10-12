/**
 * /konto/ decides from this which switch to offer, so a wrong answer is a
 * button the backend refuses: «Koble til Google» on a BankID account, or no
 * way back to e-mail from one.
 */

import { describe, expect, it } from 'vitest';

import { describeSignIn, isBankidAccount } from './signin';

describe('isBankidAccount', () => {
  it('is a BankID account exactly when BankID is linked', () => {
    expect(isBankidAccount({ has_password: false, providers: ['bankid'] })).toBe(true);
    expect(isBankidAccount({ has_password: true, providers: [] })).toBe(false);
    expect(isBankidAccount({ has_password: false, providers: ['google'] })).toBe(false);
  });
});

describe('describeSignIn', () => {
  it('names BankID alone', () => {
    expect(describeSignIn({ has_password: false, providers: ['bankid'] })).toBe(
      'Du logger inn med BankID.',
    );
    // A row left from before the rule does not make it read as two ways in.
    expect(describeSignIn({ has_password: true, providers: ['bankid', 'google'] })).toBe(
      'Du logger inn med BankID.',
    );
  });

  it('says e-mail and password, and the providers beside them', () => {
    expect(describeSignIn({ has_password: true, providers: [] })).toBe(
      'Du logger inn med e-post og passord.',
    );
    expect(describeSignIn({ has_password: true, providers: ['google'] })).toBe(
      'Du logger inn med e-post og passord eller Google.',
    );
    expect(describeSignIn({ has_password: false, providers: ['google', 'microsoft'] })).toBe(
      'Du logger inn med Google eller Microsoft.',
    );
    expect(describeSignIn({ has_password: true, providers: ['google', 'microsoft'] })).toBe(
      'Du logger inn med e-post og passord, Google eller Microsoft.',
    );
  });
});
