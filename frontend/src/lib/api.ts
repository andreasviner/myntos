/**
 * Typed client for the Myntos API.
 *
 * The site is static and the API lives on its own origin, so every call is a
 * cross-origin fetch with `credentials: 'include'`, because the session cookie is
 * httpOnly and Secure, which means no script here can read it, only send it.
 * That is the whole point: an XSS bug on this page cannot steal a login.
 *
 * The API answers errors as `{detail, code}`. `detail` is Norwegian prose meant
 * for the user; `code` is the stable identifier to branch on. Never branch on
 * `detail`.
 *
 * Every call goes through `request`, which is also where the wall between two
 * companies is enforced on this side: see `assertCompanyScope` below and
 * `lib/tenant.ts` for what it is defending against.
 */

import type { FeedbackKind, FeedbackStatus } from './feedback';
import { activeCompanyId } from './tenant';

const configured = import.meta.env.PUBLIC_API_BASE_URL;

/** Where the backend lives. Set PUBLIC_API_BASE_URL at build time. */
export const API_BASE_URL = (configured ?? 'http://localhost:8000').replace(/\/+$/, '');

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }

  /** True when the caller simply is not signed in, not an error worth showing. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

interface ValidationIssue {
  loc?: unknown[];
  msg?: string;
}

function messageFrom(status: number, body: unknown): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    // FastAPI's own request validation answers with a list of issues.
    if (Array.isArray(detail)) {
      const messages = (detail as ValidationIssue[])
        .map((issue) => issue.msg)
        .filter((msg): msg is string => typeof msg === 'string');
      if (messages.length > 0) return messages.join('. ');
    }
  }
  if (status >= 500) return 'Noe gikk galt hos oss. Prøv igjen om litt.';
  return `Forespørselen feilet (${status}).`;
}

/**
 * Refused because the data would have belonged to another company.
 *
 * An `ApiError` so that every page's existing error handling shows the message
 * rather than falling back to "noe gikk galt"; its own class so a caller that
 * genuinely means to reach across companies can tell this apart from a server
 * that said no. `status` is 0 for the same reason a network failure is: no
 * server ever answered this.
 */
export class WrongCompanyError extends ApiError {
  /** The company the page is about, and the one that was asked for or answered. */
  readonly expected: number | null;
  readonly actual: number;

  constructor(message: string, expected: number | null, actual: number) {
    super(0, message, 'wrong-company');
    this.name = 'WrongCompanyError';
    this.expected = expected;
    this.actual = actual;
  }
}

/** `/api/v1/companies/12` and everything under it, which is most of the API. */
const COMPANY_SCOPED = /^\/api\/v1\/companies\/(\d+)(?:[/?]|$)/;

/**
 * The company a path is about, or null for the account-wide endpoints.
 *
 * Read off the URL rather than passed in, so it cannot be omitted: a new
 * endpoint under `/companies/{id}/` is walled the moment it is written, without
 * its author having to know this exists.
 */
function companyInPath(path: string): number | null {
  const match = COMPANY_SCOPED.exec(path);
  return match === null ? null : Number(match[1]);
}

/**
 * Refuse to ask for, or to hand back, another company's data.
 *
 * Two checks against the id in the URL. Outward: it must be the company the
 * page is about (`lib/tenant.ts`), so a stale `Company` object held across a
 * switch, or a list built from another account, fails here instead of quietly
 * replacing the figures under a name that did not change. Inward: every
 * company-scoped answer the API sends names its own company, so a mismatch is
 * refused rather than rendered.
 *
 * The inward half has never caught anything, and the backend has two test files
 * whose whole job is to keep it that way. The point is that it goes on looking
 * without anybody remembering to.
 */
function assertCompanyScope(companyId: number, body: unknown): void {
  const rows = Array.isArray(body) ? body : [body];
  for (const row of rows) {
    if (typeof row !== 'object' || row === null) continue;
    const owner = (row as { company_id?: unknown }).company_id;
    if (typeof owner !== 'number' || owner === companyId) continue;
    throw new WrongCompanyError(
      'Vi stoppet visningen: svaret fra serveren gjaldt et annet selskap.',
      companyId,
      owner,
    );
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Before the fetch, not after: a request for the wrong company must not be
  // sent, let alone have its answer painted. Silent when no company has been
  // chosen yet: a fresh browser has nothing for the request to contradict, and
  // the first company-scoped call of a session is made on the way to settling
  // the choice.
  const scope = companyInPath(path);
  const active = activeCompanyId();
  if (scope !== null && active !== null && scope !== active) {
    throw new WrongCompanyError(
      'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
      active,
      scope,
    );
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: 'include',
      ...init,
    });
  } catch {
    // A network failure and a CORS rejection are indistinguishable here, and
    // the second one is by far the likelier during setup.
    throw new ApiError(0, 'Fikk ikke kontakt med serveren.', 'network');
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const code =
      typeof body === 'object' && body !== null && typeof (body as { code?: unknown }).code === 'string'
        ? (body as { code: string }).code
        : null;
    throw new ApiError(response.status, messageFrom(response.status, body), code);
  }
  if (scope !== null) assertCompanyScope(scope, body);
  return body as T;
}

function json<T>(path: string, method: string, payload: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// --- Accounts and organizations -------------------------------------------------

/**
 * The caller's standing on one account, which is now a thin thing.
 *
 * An account owns the companies set up under it and nothing else anybody looks
 * at. What a person may do with a set of books, and what those books cost, come
 * back with the books (`Company.role`, `Company.permissions`, `Company.plan`).
 *
 * There is no name here on purpose, and no page may print one. An account is
 * routinely called after the first company set up under it, and a name like
 * that shown anywhere near a *different* company reads as that company's data
 * having crossed over. See docs/company-isolation.md.
 */
export interface Membership {
  organization_id: number;
  role: string;
  /** What this role may do on the account: only ever `organization:*` here. */
  permissions: string[];
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  email_verified: boolean;
  has_password: boolean;
  providers: string[];
}

export interface Session {
  user: User;
  memberships: Membership[];
}

export interface ProviderSummary {
  /** Matches the path segment in /auth/{code}/start. */
  code: string;
  label: string;
}

export interface Providers {
  password: boolean;
  /** The enforced minimum. Never hardcode this number in a form hint. */
  password_min_length: number;
  /** Only the providers this deployment has actually configured. */
  providers: ProviderSummary[];
  /**
   * Whether this deployment sends mail. Address confirmation and "forgot
   * password" are offered only when it does: both are a link only an inbox
   * can carry, and a button that can only fail is worse than none.
   */
  email_delivery: boolean;
}

export const getSession = (): Promise<Session> => request<Session>('/api/v1/auth/me');

let providersPromise: Promise<Providers> | null = null;

/**
 * The sign-in methods and password policy this deployment offers.
 *
 * Cached for the life of the page: the answer is deployment configuration, and
 * both the provider buttons and the password hint need it. A rejection is not
 * cached, or one blocked request would leave the page permanently degraded.
 */
export const getProviders = (): Promise<Providers> => {
  providersPromise ??= request<Providers>('/api/v1/auth/providers').catch((error: unknown) => {
    providersPromise = null;
    throw error;
  });
  return providersPromise;
};

export const login = (email: string, password: string): Promise<Session> =>
  json<Session>('/api/v1/auth/login', 'POST', { email, password });

export const registerAccount = (payload: {
  email: string;
  password: string;
  full_name: string;
  organization_name?: string | null;
}): Promise<Session> => json<Session>('/api/v1/auth/register', 'POST', payload);

export const logout = (): Promise<void> => request<void>('/api/v1/auth/logout', { method: 'POST' });

export const acceptInvitation = (token: string): Promise<Session> =>
  json<Session>('/api/v1/auth/invitations/accept', 'POST', { token });

/** What an invitation says about itself, before anyone has signed in. */
export interface InvitationLookup {
  /** The books being shared. Never the account: that is not what is granted. */
  company_name: string;
  /** The address it was sent to. Any other account is refused the acceptance. */
  email: string;
  role: string;
  expires_at: string;
  /** Null when the account that sent it is gone. The invitation still works. */
  inviter_name: string | null;
  /**
   * Who the inviter addressed it to, when they named somebody.
   *
   * Offered as the name on the account being created, so a person invited by
   * name does not retype what the invitation already says.
   */
  full_name: string | null;
}

/**
 * Read an invitation without accepting it, and without being signed in.
 *
 * This is what lets the page an invitation link opens say who is asking and
 * into what, before it asks anybody to make an account. A 400
 * `invalid_invitation` is the one answer for every unusable token: unknown,
 * already used, or expired.
 *
 * POST, with the token in the body, for the reason the acceptance is a POST: a
 * secret in a query string ends up in access logs and Referer headers.
 */
export const lookupInvitation = (token: string): Promise<InvitationLookup> =>
  json<InvitationLookup>('/api/v1/auth/invitations/lookup', 'POST', { token });

// --- Links mailed to a person's own address ---------------------------------------
//
// Every token goes in a POST body, never a query string, for the reason the
// invitation lookup gives. The pages read it from the fragment of the mailed
// link (see lib/links.ts), which never reaches any server at all.

/** Confirm an address with the link mailed to it. Signs nobody in. */
export const verifyEmail = (token: string): Promise<void> =>
  json<void>('/api/v1/auth/email/verify', 'POST', { token });

/**
 * Mail the signed-in account a fresh confirmation link; the previous one dies.
 * A 429 `account_mail_rate_limited` is the hourly cap, and its message says so.
 */
export const sendVerificationMail = (): Promise<{ sent: boolean }> =>
  request<{ sent: boolean }>('/api/v1/auth/email/verification', { method: 'POST' });

/**
 * Ask for a reset link. The answer is the same 202 whether or not the address
 * has an account, so the page must say the same thing either way.
 */
export const forgotPassword = (email: string): Promise<void> =>
  json<void>('/api/v1/auth/password/forgot', 'POST', { email });

/**
 * Set a new password with a reset link. Ends every session of that account.
 *
 * A 409 `bankid_account` is a link for an account that signs in with BankID,
 * which a reset never gives a password: its way to one is the switch below.
 */
export const resetPassword = (token: string, newPassword: string): Promise<void> =>
  json<void>('/api/v1/auth/password/reset', 'POST', { token, new_password: newPassword });

// --- Switching between BankID and e-mail ------------------------------------------
//
// An account that signs in with BankID signs in with nothing else: BankID's
// terms forbid using a BankID login to set up or restore a sign-in of our own
// (docs/integrations/bankid.md). So both directions are a switch that takes the
// other way in away, and neither is an ordinary link.

/**
 * Start switching the signed-in account to BankID, and get the BankID page to
 * send the browser to.
 *
 * A POST, not a `providerStartUrl` link: when it completes, the password and
 * any Google or Microsoft sign-in are gone and every other session is ended,
 * so the backend wants the password first (`password` is ignored for an
 * account without one, which needs a login in the last ten minutes instead).
 * A 403 `reauthentication_required` or `reauthentication_failed` says which
 * proof was missing.
 */
export const startBankidLink = (
  password: string | null,
  redirectTo: string,
): Promise<{ authorization_url: string }> =>
  json<{ authorization_url: string }>('/api/v1/auth/bankid/link', 'POST', {
    password,
    redirect_to: redirectTo,
  });

/**
 * Mail a BankID account the link that moves it to e-mail and password.
 *
 * Nothing changes until the link is used; `email` is the address it went to,
 * which is always the account's own. A 429 is the hourly cap.
 */
export const requestSwitchToEmail = (): Promise<{ sent: boolean; email: string }> =>
  request<{ sent: boolean; email: string }>('/api/v1/account/switch-to-email', { method: 'POST' });

/**
 * Finish the switch with the mailed link: the password is set, BankID removed,
 * and every session ended. A 400 `invalid_email_token` is every unusable link.
 */
export const confirmSwitchToEmail = (token: string, newPassword: string): Promise<void> =>
  json<void>('/api/v1/auth/switch-to-email/confirm', 'POST', {
    token,
    new_password: newPassword,
  });

/**
 * What a federated flow is started for, which the backend needs to know before
 * the provider answers.
 *
 * `login` signs in a linked account. `signup` does the same, except that a
 * BankID linked to nothing goes on to `/fullfor-registrering/` to ask for an
 * address instead of being refused. `link` attaches Google or Microsoft to the
 * account that is signed in, from `/konto/`. For Google and Microsoft, which
 * carry an address, `login` and `signup` behave the same.
 *
 * BankID is never linked with `link`: the backend refuses it, because linking
 * BankID is a switch that removes every other way in. `startBankidLink` is the
 * way, and it asks for the password first.
 */
export type ProviderIntent = 'login' | 'signup' | 'link';

/** Where to send the browser to start a federated flow. */
export const providerStartUrl = (
  code: string,
  redirectTo: string,
  intent: ProviderIntent = 'login',
): string =>
  `${API_BASE_URL}/api/v1/auth/${encodeURIComponent(code)}/start` +
  `?redirect_to=${encodeURIComponent(redirectTo)}` +
  (intent === 'login' ? '' : `&intent=${intent}`);

/** A BankID signup held by the backend while the person types an address. */
export interface HeldProviderSignup {
  provider: string;
  provider_label: string;
  /** From BankID. Shown, not editable: that BankID vouched for it is the point. */
  full_name: string;
  expires_at: string;
  /** Where the person was going when they started, such as an invitation. */
  redirect_to: string | null;
}

/** Read a held signup. A 400 `invalid_provider_signup` is every unusable token. */
export const lookupProviderSignup = (token: string): Promise<HeldProviderSignup> =>
  json<HeldProviderSignup>('/api/v1/auth/provider-signup/lookup', 'POST', { token });

/**
 * Create the account a held signup was waiting for, signed in.
 *
 * A 409 `email_already_registered` means the address has an account. The hold
 * survives it, and that account is reached by signing in to it and switching it
 * to BankID from `/konto/`, never from here.
 *
 * The account this makes signs in with BankID and nothing else.
 */
export const completeProviderSignup = (payload: {
  token: string;
  email: string;
  organization_name?: string | null;
}): Promise<Session> => json<Session>('/api/v1/auth/provider-signup', 'POST', payload);

// --- The signed-in person's own account ---------------------------------------------

/**
 * One place the person is signed in.
 *
 * `device` is the backend's coarse "Firefox på Linux", or null when the browser
 * said nothing it recognised. The raw user agent and the IP address are never
 * sent: see backend/src/myntos/auth/devices.py for why.
 */
export interface AccountSession {
  id: number;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  device: string | null;
  current: boolean;
}

export const listAccountSessions = (): Promise<AccountSession[]> =>
  request<AccountSession[]>('/api/v1/account/sessions');

export const endAccountSession = (sessionId: number): Promise<void> =>
  request<void>(`/api/v1/account/sessions/${sessionId}`, { method: 'DELETE' });

export const endOtherAccountSessions = (): Promise<void> =>
  request<void>('/api/v1/account/sessions/revoke-others', { method: 'POST' });

/**
 * One provider the person can sign in with. `removable` is the backend's
 * answer to "would there still be a way in", so the page never offers a
 * button it would refuse. Never true for BankID, which is left only by
 * switching to e-mail.
 */
export interface AccountIdentity {
  provider: string;
  label: string;
  linked_at: string;
  last_login_at: string | null;
  removable: boolean;
}

export const listAccountIdentities = (): Promise<AccountIdentity[]> =>
  request<AccountIdentity[]>('/api/v1/account/identities');

/**
 * A 409 `last_sign_in_method` is the backend keeping a way into the account,
 * and a 409 `bankid_account` is BankID, which is never removed this way.
 */
export const unlinkAccountIdentity = (provider: string): Promise<void> =>
  request<void>(`/api/v1/account/identities/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
  });

/**
 * What deleting the account would do to one set of books the person is on.
 *
 * `leaves`: their access goes and the books carry on. `deleted`: nothing was
 * entered and nobody else is on them, so they go too. `blocks_*`: they are the
 * last owner of books somebody still needs, and the deletion is refused until
 * they hand them on.
 */
export type CompanyFate = 'leaves' | 'deleted' | 'closed' | 'blocks_members' | 'blocks_books';

/**
 * Carries `company_id` per row but lives outside `/companies/{id}/`, so the
 * company wall in `request()` does not inspect it: it is a list of every set of
 * books the person is on, by design, and none of it is painted as "this company".
 */
export interface ErasurePlan {
  companies: { company_id: number; company_name: string; fate: CompanyFate }[];
  keeps_name: boolean;
  blocked: boolean;
  requires_password: boolean;
  recent_login_until: string | null;
}

export const getErasurePlan = (): Promise<ErasurePlan> =>
  request<ErasurePlan>('/api/v1/account/erasure');

/** Delete the account. `password` is required when the account has one. */
export const eraseAccount = (password: string | null): Promise<void> =>
  json<void>('/api/v1/account/erasure', 'POST', { password });

// --- Deleting a company ----------------------------------------------------------
//
// A company with anything entered is not deleted at once: bokføringsloven § 13
// keeps its books for five years after the last financial year, so they are
// locked away until `retain_until` and deleted for good the day after. One with
// nothing entered goes at once. See docs/gdpr.md, «Deleting a company».

export interface ClosurePlan {
  /** `kept`: locked away until `retain_until`. `deleted`: gone at once. */
  outcome: 'kept' | 'deleted';
  retain_until: string | null;
  /** Years with vouchers, oldest first: one SAF-T file each to download first. */
  years: number[];
  members_leaving: number;
  live_keys: number;
  requires_password: boolean;
  recent_login_until: string | null;
}

export const getClosurePlan = (companyId: number): Promise<ClosurePlan> =>
  request<ClosurePlan>(`/api/v1/companies/${companyId}/sletting`);

/**
 * Delete the company. `companyName` is what the person typed to say which books
 * they meant. `password` is sent only when the account has one
 * (`requires_password`); an account that signed up with BankID, Google or
 * Microsoft sends null and must have signed in within the last ten minutes.
 */
export const closeCompany = (
  companyId: number,
  companyName: string,
  password: string | null,
): Promise<{ outcome: ClosurePlan['outcome']; retain_until: string | null }> =>
  json(`/api/v1/companies/${companyId}/sletting`, 'POST', {
    company_name: companyName,
    password,
  });

/**
 * A company the person deleted and still owns, while its books are kept.
 *
 * Outside `/companies/{id}/` on purpose, like the erasure plan: these are books
 * nobody is working in, and none of them is ever the active company.
 */
export interface ClosedCompany {
  id: number;
  orgnr: string;
  name: string;
  closed_at: string;
  /** Deleted for good the day after. */
  retain_until: string;
}

export const listClosedCompanies = (): Promise<ClosedCompany[]> =>
  request<ClosedCompany[]>('/api/v1/companies/slettede');

/** Open deleted books again. Answers with the company, ready to be made active. */
export const reopenCompany = (companyId: number): Promise<Company> =>
  json<Company>(`/api/v1/companies/slettede/${companyId}/gjenapne`, 'POST', {});

// --- Companies ------------------------------------------------------------------

export interface BankAccount {
  id: number;
  /** Eleven digits, no separators. `kontonummer()` in `lib/format.ts` groups it. */
  account: string;
  /** What the owner calls it — «Drift», «Skattetrekk». Null when unnamed. */
  label: string | null;
  /**
   * The account an invoice is paid into when it names none itself.
   *
   * Exactly one carries it for as long as the company has any account at all,
   * which is why the list can be read as "the first one is where money goes".
   */
  is_primary: boolean;
}

export interface Company {
  id: number;
  organization_id: number;
  orgnr: string;
  name: string;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  currency_code: string;
  /** Decides the "MVA" suffix after the orgnr, and whether VAT may be charged at all. */
  vat_registered: boolean;
  /**
   * The day the registration took effect, ISO date, when later than the start of
   * the books. Entries dated before it get no VAT split. Null means "registered
   * for as long as the books go back".
   */
  vat_registered_from: string | null;
  /** Decides the "Foretaksregisteret" marking every AS and NUF must carry. */
  in_foretaksregisteret: boolean;
  organisation_form: string | null;
  /**
   * Every account the company is paid into, primary first.
   *
   * A list and not a field: a business with a driftskonto and a
   * skattetrekkskonto has two, and which one an invoice names is a per-invoice
   * choice with `is_primary` as its default. Empty until someone adds one, and
   * an invoice cannot be issued while it is, because there would be nowhere to
   * pay it (see the `seller_bank_account` violation).
   */
  bank_accounts: BankAccount[];
  /**
   * Whether this company's bilag may be shown to a model at all.
   *
   * Per company rather than per user: it is a statement about a set of books,
   * and it has to hold whoever happens to be logged in. Off means no upload to
   * Løynd anywhere in the app, not merely a hidden button.
   */
  ki_enabled: boolean;
  /**
   * Whether keys for agents and integrations may be made for these books.
   *
   * Off until an owner or admin turns it on under Tilgang, after reading what
   * it means. Turning it off stops every key, for good. Changed only through
   * `setAgentAccess`, never through `updateCompany`.
   */
  agent_access_enabled: boolean;
  /**
   * Whether somebody has proved through Altinn that they may act for this orgnr.
   *
   * False is the ordinary state for books somebody just typed an orgnr into,
   * and the header says «ubekreftet» rather than hiding anything: bookkeeping
   * is not gated on it, ownership is. `verifyCompany` is what changes it.
   */
  verified: boolean;
  /** When ownership was confirmed. ISO 8601, or null while unconfirmed. */
  verified_at: string | null;

  /**
   * What these books cost, and therefore what they may use.
   *
   * On the company and not on the account: a set of books is the thing that has
   * a size, so a regnskapsfører with twenty clients is twenty subscriptions
   * rather than one, and a private person with one company is not priced as
   * though they might have twenty. Nothing enforces it yet.
   */
  plan: string;
  /**
   * The last day of the books when the company has been dissolved (oppløst),
   * as an ISO date; null while it is in business. Nothing may be posted after
   * it, and its year's skattemelding is a forhåndsfastsetting. Set on
   * /arsavslutning/ (`windUp`).
   */
  wound_up_on?: string | null;
  /**
   * What *this* caller may do with *these* books.
   *
   * Access is a row per person per company, so the role travels with the
   * company rather than with the session: the same person can own their own
   * books and merely read a client's. Decide what to offer from `permissions`
   * and never from `role`, for the reason in docs/frontend-conventions.md: the
   * role to permission table belongs to the backend.
   */
  role: string;
  permissions: string[];

  // --- Copied from Enhetsregisteret ---
  // Refreshed by `refreshCompany`, and automatically when a company is created
  // or confirmed. The register is the authority for all of them.
  industry_code: string | null;
  industry_description: string | null;
  website: string | null;
  phone: string | null;
  email: string | null;
  /** Set when this company is a subunit: the main unit it belongs to. */
  parent_orgnr: string | null;
  /** When the register was last read. Null until it has been read once. */
  register_synced_at: string | null;
}

export const listCompanies = (): Promise<Company[]> => request<Company[]>('/api/v1/companies');

export const createCompany = (payload: {
  organization_id: number;
  orgnr: string;
  name: string;
  first_financial_year?: number;
  address_line?: string | null;
  postal_code?: string | null;
  city?: string | null;
  vat_registered?: boolean;
  in_foretaksregisteret?: boolean;
  organisation_form?: string | null;
  bank_account?: string | null;
}): Promise<Company> => json<Company>('/api/v1/companies', 'POST', payload);

/**
 * Correct the company's own details. Every omitted field is left alone.
 *
 * `orgnr` is editable only while the company is unconfirmed, which is what
 * makes a typo in nine digits a correction rather than a rebuild. Once somebody
 * has confirmed ownership in Altinn the number is settled, and a changed one is
 * refused with 409 `company_orgnr_locked`; 409 `duplicate_company` is the other
 * refusal, for a number this organization already keeps books under.
 */
export const updateCompany = (
  companyId: number,
  payload: {
    orgnr?: string;
    name?: string;
    address_line?: string | null;
    postal_code?: string | null;
    city?: string | null;
    vat_registered?: boolean;
    /** Null clears it; leaving the key out leaves it alone. */
    vat_registered_from?: string | null;
    in_foretaksregisteret?: boolean;
    organisation_form?: string | null;
    ki_enabled?: boolean;
  },
): Promise<Company> => json<Company>(`/api/v1/companies/${companyId}`, 'PATCH', payload);

/**
 * Add an account the company is paid into.
 *
 * `account` may carry the separators people type (`1506.20.33158`) or a
 * Norwegian IBAN; the backend normalises it and verifies the mod-11 check
 * digit. Two refusals worth telling apart: 422 `invalid_bank_account` for a
 * number that cannot be one, and 409 `duplicate_bank_account` for one this
 * company already holds.
 *
 * The first account added is the primary whatever `is_primary` says: a company
 * with accounts and no default has nowhere for its next invoice to be paid.
 *
 * Answers with the whole company, like the other two below, because promoting
 * an account changes another account's row as well — "the account that changed"
 * was never one row. Hand the answer to `setActiveCompany` the same way an
 * `updateCompany` answer is handled.
 */
export const addBankAccount = (
  companyId: number,
  payload: { account: string; label?: string | null; is_primary?: boolean },
): Promise<Company> =>
  json<Company>(`/api/v1/companies/${companyId}/bankkontoer`, 'POST', payload);

/**
 * Correct a number, rename an account, or make it the one invoices default to.
 *
 * Every omitted field is left alone; an empty `label` clears the name.
 * `is_primary: true` promotes this account and demotes whichever held it.
 * `is_primary: false` is refused on the account that currently is primary, with
 * 409 `primary_bank_account_required` — the way to move the marking is to
 * promote the account it should move to.
 */
export const updateBankAccount = (
  companyId: number,
  bankAccountId: number,
  payload: { account?: string; label?: string | null; is_primary?: boolean },
): Promise<Company> =>
  json<Company>(`/api/v1/companies/${companyId}/bankkontoer/${bankAccountId}`, 'PATCH', payload);

/**
 * Close an account. Nothing that has already been issued changes.
 *
 * An issued salgsdokument carries its own copy of the number it named, and a
 * draft pointed here falls back to the primary the next time it is saved.
 * Deleting the primary promotes the oldest account that remains.
 */
export const deleteBankAccount = (companyId: number, bankAccountId: number): Promise<Company> =>
  request<Company>(`/api/v1/companies/${companyId}/bankkontoer/${bankAccountId}`, {
    method: 'DELETE',
  });

/**
 * Confirm through Altinn that these books belong to the caller.
 *
 * Requires the ID-porten login, and reads the same list `listAuthorizedParties`
 * does. Three rejections worth branching on, by `code` and status:
 *
 * - **409, no code** — no usable government login. Offer the ID-porten button.
 * - **403 `not_authorized_for_party`** — Altinn does not list this orgnr for
 *   this person. Rights are granted in Altinn, not here.
 * - **409 `company_already_claimed`** — another account confirmed this orgnr
 *   first. Ask the person whether to go on, and call again with `evenIfClaimed`
 *   if they say yes. `confirmOwnership` in `lib/ownership.ts` does both; go
 *   through it rather than calling this directly.
 *
 * `evenIfClaimed` is the person's yes to that question, and is only ever sent
 * after they have been asked.
 */
export const verifyCompany = (companyId: number, evenIfClaimed = false): Promise<Company> =>
  evenIfClaimed
    ? json<Company>(`/api/v1/companies/${companyId}/bekreft`, 'POST', { even_if_claimed: true })
    : request<Company>(`/api/v1/companies/${companyId}/bekreft`, { method: 'POST' });

/** The code a 409 from `verifyCompany` carries when somebody confirmed it first. */
export const ALREADY_CLAIMED = 'company_already_claimed';

/** The code a 409 from `updateCompany` carries when the orgnr is settled. */
export const ORGNR_LOCKED = 'company_orgnr_locked';

// --- Enhetsregisteret --------------------------------------------------------------

/** Registered share capital, for the organisation forms that have one. */
export interface Capital {
  /** A decimal string, never a number: kroner do not survive a float. */
  amount: string;
  currency: string;
  shares: number | null;
}

/**
 * What Brønnøysundregistrene has on file for an organisasjonsnummer.
 *
 * Everything the register holds, not the handful the signup form fills in: the
 * lookup is one request either way, and each field left out is a field somebody
 * types instead.
 */
export interface RegisteredUnit {
  orgnr: string;
  name: string;
  organisation_form: string | null;
  organisation_form_description: string | null;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  registered_in_vat_register: boolean;
  /** Decides the "Foretaksregisteret" marking § 5-1-2 requires on every invoice. */
  registered_in_foretaksregisteret: boolean;
  bankrupt: boolean;
  under_liquidation: boolean;
  under_forced_liquidation: boolean;
  municipality: string | null;
  municipality_number: string | null;
  country: string | null;
  /** Only set when it differs from the business address — usually a postboks. */
  postal_address_line: string | null;
  postal_address_postal_code: string | null;
  postal_address_city: string | null;
  /** SN2007. The næringsspesifikasjon asks for the first one. */
  industry_code: string | null;
  industry_description: string | null;
  /** Up to two more, as [code, description] pairs. */
  secondary_industries: [string, string][];
  website: string | null;
  phone: string | null;
  mobile: string | null;
  email: string | null;
  /** ISO dates, as the register gives them. */
  established_on: string | null;
  registered_on: string | null;
  registered_in_foretaksregisteret_on: string | null;
  registered_in_vat_register_on: string | null;
  /** The last annual accounts the register has received, as a year. */
  last_submitted_annual_accounts: string | null;
  /**
   * The day the register deleted the unit, as an ISO date; null for a live one.
   * A deleted unit carries little more than its name and form, and is shown as
   * deleted («slettet») wherever «konkurs» would be.
   */
  deleted_on?: string | null;
  /** Set on a subunit: the main unit it belongs to, and where its roles live. */
  parent_orgnr: string | null;
  in_group: boolean;
  /** Null when nobody has reported one, which is not the same as zero. */
  employees: number | null;
  language: string | null;
  sector_code: string | null;
  sector_description: string | null;
  capital: Capital | null;
  purpose: string[];
  activity: string[];
  /** Names this unit used to have. */
  former_names: string[];
  registered_in_frivillighetsregisteret: boolean;
  registered_in_stiftelsesregisteret: boolean;
  is_subunit: boolean;
}

/**
 * One registered role: a board member, a daglig leder, a revisor.
 *
 * Names only. The register pairs a person with a birth date; that is personal
 * data with no bookkeeping use and the backend drops it.
 */
export interface RegisteredRole {
  /** The rollegruppe: `STYR`, `DAGL`, `REVI`, `REGN`, `INNH`… */
  group: string;
  group_description: string;
  /** The role within it: `LEDE`, `MEDL`, `VARA`… */
  code: string;
  description: string;
  name: string;
  /** Set when the holder is a company, as revisor and regnskapsfører usually are. */
  orgnr: string | null;
  /** "Representant for de ansatte" and the like, when the register says. */
  elected_by: string | null;
}

/**
 * Whether a registered role holder can open the books.
 *
 * The register gives a name and nothing else, so this is a name match and the
 * value says how much that match is worth:
 *
 * - `bekreftet`: a member whose ID-porten login carries this name. Both names
 *   come from Folkeregisteret, so it is the same person twice.
 * - `trolig`: a member whose *account* name matches. Probably them; nobody
 *   has proved it.
 * - `invitert`: asked, not yet joined.
 * - `ingen`: nobody in the organization goes by this name.
 * - `selskap`: the holder is a revisor or regnskapsfører firm, so the
 *   question does not apply.
 *
 * It grants nothing and enforces nothing. Only `/companies/{id}/register` and
 * `/companies/{id}/hent` fill it in; the bare Enhetsregisteret lookup has no
 * organization to resolve it against and leaves it at `ingen`.
 */
export type RegisterAccess = 'bekreftet' | 'trolig' | 'invitert' | 'ingen' | 'selskap';

/** A registered role, plus what is known about that person's access. */
export interface RegisteredRoleWithAccess extends RegisteredRole {
  access: RegisterAccess;
  /** The organization role they hold, or would hold on accepting. */
  access_role: string | null;
  /** The Myntos-side name, when it is not the register's own spelling. */
  access_name: string | null;
  /** Where a pending invitation went. */
  access_email: string | null;
}

/**
 * Look up a company or party by organisasjonsnummer.
 *
 * Rejects with an `ApiError` whose `code` distinguishes the cases worth telling
 * apart: `invalid_orgnr` (the number cannot be right), `unit_not_found`,
 * `unit_deleted`, and `register_unavailable` (ask again later, or type it in).
 */
export const lookupUnit = (orgnr: string): Promise<RegisteredUnit> =>
  request<RegisteredUnit>(`/api/v1/enhetsregisteret/${encodeURIComponent(orgnr)}`);

/**
 * Who holds a registered role in the unit: styre, daglig leder, revisor…
 *
 * A second request to Brønnøysund, so it is asked for separately: the signup
 * form wants a name and an address, and this is for the page that shows who the
 * company is. A subunit's roles are read from its main unit by the backend.
 *
 * An empty list is a legitimate answer, not an error.
 */
export const lookupRoles = (orgnr: string): Promise<RegisteredRole[]> =>
  request<RegisteredRole[]>(`/api/v1/enhetsregisteret/${encodeURIComponent(orgnr)}/roller`);

/**
 * Everything Enhetsregisteret says about a company, and when it said it.
 *
 * The same shape whether it was just fetched or read back out of storage, so a
 * page renders one exactly as it renders the other.
 */
export interface RegisterAnswer {
  unit: RegisteredUnit;
  roles: RegisteredRoleWithAccess[];
  /** ISO 8601. Shown beside the data: a cached answer has to say how old it is. */
  fetched_at: string;
}

/** A company after a register refresh, and everything the register knows. */
export interface CompanyRefresh extends RegisterAnswer {
  company: Company;
  /** Which stored fields the register moved. Empty when it agreed with all. */
  changed: string[];
}

/**
 * What the register last said, without asking it again.
 *
 * `null` when nothing has been stored yet — a company set up while Brønnøysund
 * was down — or when everything stored describes an orgnr the company no longer
 * has. This is what a page load should use; `refreshCompany` is how you ask for
 * a fresh answer, and it needs the permission that editing the company needs.
 */
export const getCompanyRegister = (companyId: number): Promise<RegisterAnswer | null> =>
  request<RegisterAnswer | null>(`/api/v1/companies/${companyId}/register`);

/**
 * Re-read everything Enhetsregisteret says about this company.
 *
 * Businesses move, cross the VAT threshold and change their næring, and none of
 * those tells Myntos anything — this is how the stored copy catches up. The
 * name is deliberately never overwritten: a name somebody edited on purpose and
 * one that has gone stale are indistinguishable from the server.
 *
 * Also runs by itself when a company is created and when one is confirmed.
 */
export const refreshCompany = (companyId: number): Promise<CompanyRefresh> =>
  request<CompanyRefresh>(`/api/v1/companies/${companyId}/hent`, {
    method: 'POST',
  });

// --- Altinn: the companies the person may report for -------------------------------

/** Whether an ID-porten login exists, and who it belongs to. */
export interface GovernmentLoginStatus {
  /** False when the deployment has no ID-porten client. Offer nothing then. */
  available: boolean;
  logged_in: boolean;
  /** The person who logged in. A fødselsnummer, so it is shown, never stored. */
  pid: string | null;
  acr: string | null;
  expires_at: string | null;
  /**
   * Whether this account has ever proved who it belongs to.
   *
   * Outlives `logged_in`, which is about an authorization that expires within
   * the hour. "Have you shown us who you are" and "can you file right now" are
   * different questions and the interface asks both.
   */
  linked: boolean;
  /** The name ID-porten gave. Null when the deployment lacks the `profile` scope. */
  linked_name: string | null;
  linked_at: string | null;
}

export const getGovernmentLoginStatus = (): Promise<GovernmentLoginStatus> =>
  request<GovernmentLoginStatus>('/api/v1/skatt/status');

/**
 * Where to send the browser to log in with ID-porten.
 *
 * A full page navigation, like the federated sign-in above and for the same
 * reason: BankID will not run in a fetch. `redirectTo` is validated
 * server-side as site-relative, so it cannot become an open redirect.
 */
export const governmentLoginStartUrl = (redirectTo: string): string =>
  `${API_BASE_URL}/api/v1/skatt/idporten/start?redirect_to=${encodeURIComponent(redirectTo)}`;

/** One company Altinn says the logged-in person may report for. */
export interface AuthorizedParty {
  orgnr: string;
  name: string;
  /** Altinn's own key for the party, carried through to a later submission. */
  party_id: number;
  /** "AS", "ENK", "BEDR"… when Altinn knows it. */
  unit_type: string | null;
  /** The main unit, when this is a subunit of one. */
  parent_orgnr: string | null;
  /** Altinn's own role codes: DAGL, REGN, LEDE. Not translated. */
  roles: string[];
  /**
   * True when another organization has already confirmed ownership of this
   * orgnr, so confirming it here asks «Er du sikker?» first.
   *
   * Said next to the choice rather than after a failed save. Which organization
   * holds the claim is deliberately not disclosed: the caller is not a member
   * of it, and the way in is to ask them for access.
   */
  claimed: boolean;
}

/**
 * The companies the person may report for, from Altinn's access management.
 *
 * Requires the ID-porten login, and does not spend it. Rejects with 409 when
 * there is none — check `getGovernmentLoginStatus()` first; the 409 is the
 * answer to a login that expired between the two calls, not the normal path.
 */
export const listAuthorizedParties = (): Promise<AuthorizedParty[]> =>
  request<AuthorizedParty[]>('/api/v1/skatt/altinn/authorized-parties');

// --- Members and invitations -----------------------------------------------------

export interface Member {
  user_id: number;
  email: string;
  full_name: string;
  role: string;
  is_self: boolean;
  /**
   * Whether this colleague has proved through ID-porten who they are.
   *
   * The fact travels; the fødselsnummer behind it never leaves the server.
   */
  government_linked: boolean;
  /** The name ID-porten gave, when there is one. */
  government_name: string | null;
  /** ISO 8601, when the link was last renewed by a login. */
  government_verified_at: string | null;
}

export interface Invitation {
  id: number;
  email: string;
  role: string;
  expires_at: string;
  /** Who it was addressed to, when the inviter named somebody. */
  full_name: string | null;
  /** ISO 8601. Null only on responses that predate the field. */
  created_at: string | null;
  /** The colleague who sent it. Null once that account is gone. */
  invited_by: string | null;
  /** Only ever populated on the response that created it, since the token is stored hashed. */
  invite_url: string | null;
  /**
   * Whether Myntos emailed the invitation.
   *
   * False covers both a deployment with no mail configured and a message the
   * relay refused; either way the invitation exists and the link is the way
   * round it. Null on the listing, which is not about a fresh invitation and
   * where nobody is waiting to hear.
   */
  email_sent: boolean | null;
}

// Members and invitations belong to one company, not to the account. Inviting
// somebody to help with one client used to hand them every client on the
// account, and the form that did it sat under the account, where nothing said
// so. See docs/company-isolation.md.

export const listMembers = (companyId: number): Promise<Member[]> =>
  request<Member[]>(`/api/v1/companies/${companyId}/members`);

export const listInvitations = (companyId: number): Promise<Invitation[]> =>
  request<Invitation[]>(`/api/v1/companies/${companyId}/invitations`);

/**
 * Invite an address to one company's books, optionally naming the person.
 *
 * The name is a label and never a check: acceptance still requires signing in
 * as the invited address. What it buys is an email that greets somebody and a
 * register role list that can show a board member has been asked.
 */
export const inviteMember = (
  companyId: number,
  email: string,
  role: string,
  fullName?: string | null,
): Promise<Invitation> =>
  json<Invitation>(`/api/v1/companies/${companyId}/invitations`, 'POST', {
    email,
    role,
    full_name: fullName ?? null,
  });

/**
 * Send a pending invitation again, with a new link.
 *
 * The previous link stops working, because only the token's hash is stored and
 * there is no way to send the old one twice. The message says so. The row keeps
 * its id, its role and its name, which is the difference from revoking and
 * retyping the address.
 */
export const resendInvitation = (companyId: number, invitationId: number): Promise<Invitation> =>
  json<Invitation>(
    `/api/v1/companies/${companyId}/invitations/${invitationId}/send-again`,
    'POST',
    {},
  );

export const revokeInvitation = (companyId: number, invitationId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/invitations/${invitationId}`, {
    method: 'DELETE',
  });

export const changeMemberRole = (
  companyId: number,
  userId: number,
  role: string,
): Promise<Member> =>
  json<Member>(`/api/v1/companies/${companyId}/members/${userId}`, 'PATCH', { role });

/** Take away someone's access to these books, or give up your own. */
export const removeMember = (companyId: number, userId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/members/${userId}`, {
    method: 'DELETE',
  });

// --- Keys for agents and integrations ------------------------------------------------
//
// A person's end of docs/agents-and-api.md: switch keys on for a company, make
// one, see them, stop one. The agent's end (the token exchange) is never
// called from here; the page only hands the person something to paste.

/** What a key may attempt. `read` is always among them. */
export type AgentScope = 'read' | 'write' | 'post';

export interface AgentKey {
  id: number;
  company_id: number;
  name: string;
  /** What the key may do on its own. */
  scopes: AgentScope[];
  /**
   * What it may only ask to do: each such request waits on /godkjenning/ for
   * the key's person (docs/agents-and-api.md rule 13). Never overlaps `scopes`.
   */
  approval_scopes: AgentScope[];
  /**
   * `waiting`: made, not yet taken into use. `active`: in use. `stopped`: by a
   * person or by Myntos (`revoked_reason` says which). `expired`: past a
   * deadline nobody had to act on.
   */
  state: 'waiting' | 'active' | 'stopped' | 'expired';
  person_name: string;
  is_own: boolean;
  created_at: string;
  expires_at: string;
  connection_key_expires_at: string;
  exchanged_at: string | null;
  last_used_at: string | null;
  /** The agent's software, coarsely: "python-httpx", never a whole User-Agent. */
  last_used_client: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
}

export interface IssuedAgentKey {
  company_id: number;
  key: AgentKey;
  /** Shown once. Only its hash is stored, so it can never be shown again. */
  connection_key: string;
}

export const listAgentKeys = (companyId: number): Promise<AgentKey[]> =>
  request<AgentKey[]>(`/api/v1/companies/${companyId}/agent-keys`);

export const createAgentKey = (
  companyId: number,
  name: string,
  scopes: AgentScope[],
  approvalScopes: AgentScope[] = [],
): Promise<IssuedAgentKey> =>
  json<IssuedAgentKey>(`/api/v1/companies/${companyId}/agent-keys`, 'POST', {
    name,
    scopes,
    approval_scopes: approvalScopes,
  });

export const stopAgentKey = (companyId: number, keyId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/agent-keys/${keyId}`, { method: 'DELETE' });

/** Switch keys on or off for these books. Off stops every key for good. */
export const setAgentAccess = (companyId: number, enabled: boolean): Promise<Company> =>
  json<Company>(`/api/v1/companies/${companyId}/agent-access`, 'PUT', { enabled });

// --- Requests that wait for approval -----------------------------------------------
//
// A key whose person chose «Spør meg først» does not write on its own: each
// such request is stored and waits here until its person approves or declines
// it (docs/agents-and-api.md rule 13). The same for an AI assistant and for a
// program somebody wrote: a key is a key.

/**
 * `pending` waits. `done` ran, `failed` ran and was refused. `declined`,
 * `withdrawn`, `expired` and `key_stopped` never ran. `running` is being run,
 * or stopped mid-run.
 */
export type ApprovalState =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'declined'
  | 'withdrawn'
  | 'expired'
  | 'key_stopped';

export interface ApprovalFile {
  id: number;
  filename: string;
  content_type: string;
  size: number;
  /** A bilag added for the voucher the request makes, rather than part of the request. */
  attach: boolean;
  /** Emptied once the request no longer waits: run, it is in the bilag archive. */
  available: boolean;
}

export interface Approval {
  id: number;
  company_id: number;
  kind: 'request' | 'filing';
  status: ApprovalState;
  scope: string;
  /** The end of «<nøkkel> vil …», in bokmål: «føre en betaling». */
  label: string;
  /**
   * Running it sends something out of Myntos (to Skatteetaten, so far). Such a
   * request waits for its person even from a key that may post on its own.
   */
  sends_out: boolean;
  /**
   * It goes with the person's own ID-porten login, which they must hold when
   * they approve: 409 `id_porten_login_required` until they do.
   */
  needs_id_porten: boolean;
  /** The agent's own words about it. The agent's claim, not Myntos's. */
  note: string | null;
  /**
   * What it acts on, one line per record its path names, written by Myntos
   * from the books («Bilag 2026-0014, 14.03.2026: …»). Filled while it waits.
   */
  target: string[];
  method: string;
  path: string;
  query: string;
  body: unknown;
  form: Record<string, string[]> | null;
  files: ApprovalFile[];
  /**
   * `type` is `mva_melding` or `skattemelding`. `termin` is the MVA termin and
   * null for the yearly skattemelding; `naering` names the second melding of a
   * company that files two, and null is the main one.
   */
  filing: { type: string; aar: number; termin: number | null; naering: string | null } | null;
  key_id: number;
  key_name: string;
  person_name: string;
  is_own: boolean;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  decision_note: string | null;
  result_status: number | null;
  result: unknown;
  attachments: { filename: string; status: number; body: unknown }[];
  url: string;
}

export const listApprovals = (
  companyId: number,
  status: 'pending' | 'all' = 'all',
): Promise<Approval[]> =>
  request<Approval[]>(`/api/v1/companies/${companyId}/godkjenninger?status=${status}`);

/**
 * Approve and run it now, as the key. Only the key's own person may.
 *
 * `files` is every file the card showed. A key can add one while the card is
 * open, and the yes covers what was on the screen: a request that has another
 * set by then is refused with 409 `approval_changed` and still waits.
 */
export const approveRequest = (
  companyId: number,
  approvalId: number,
  files: number[],
): Promise<Approval> =>
  json<Approval>(`/api/v1/companies/${companyId}/godkjenninger/${approvalId}/godkjenn`, 'POST', {
    files,
  });

/** Decline it, with a reason the agent can read. */
export const declineRequest = (
  companyId: number,
  approvalId: number,
  note: string | null,
): Promise<Approval> =>
  json<Approval>(`/api/v1/companies/${companyId}/godkjenninger/${approvalId}/avvis`, 'POST', {
    note,
  });

/**
 * A file sent with a waiting request, as a blob, or null once it is gone.
 *
 * Fetched rather than linked, for the reason `fetchThumbnail` gives: a link is
 * a request the company wall never sees.
 */
export const fetchApprovalFile = async (
  companyId: number,
  approvalId: number,
  fileId: number,
): Promise<Blob | null> => {
  const path = `/api/v1/companies/${companyId}/godkjenninger/${approvalId}/fil?id=${fileId}`;
  const active = activeCompanyId();
  if (active !== null && companyId !== active) {
    throw new WrongCompanyError(
      'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
      active,
      companyId,
    );
  }
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' });
    if (!response.ok) return null;
    // The page opens this blob in a new tab, and a blob URL is our origin: an
    // HTML file opened there runs as myntos.no in the person's session. The
    // file came from a key, so only a type that cannot carry a script is kept;
    // anything else becomes bytes the browser downloads instead of showing.
    const type = (response.headers.get('Content-Type') ?? '').split(';')[0].trim().toLowerCase();
    const shown = VIEWABLE_FILE_TYPES.has(type) ? type : 'application/octet-stream';
    return new Blob([await response.blob()], { type: shown });
  } catch {
    return null;
  }
};

const VIEWABLE_FILE_TYPES = new Set([
  'application/pdf',
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/heic',
]);

// --- Customers and suppliers -------------------------------------------------------

export type PartyType = 'kunde' | 'leverandor';

export interface Party {
  id: number;
  party_type: PartyType;
  party_id: string;
  name: string;
  orgnr: string | null;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  balance_account_id: number;
}

export const listParties = (companyId: number): Promise<Party[]> =>
  request<Party[]>(`/api/v1/companies/${companyId}/parties`);

export const createParty = (
  companyId: number,
  payload: {
    party_type: PartyType;
    name: string;
    orgnr?: string | null;
    /** The number the party is known by; generated when left out. */
    party_id?: string | null;
    address_line?: string | null;
    postal_code?: string | null;
    city?: string | null;
  },
): Promise<Party> => json<Party>(`/api/v1/companies/${companyId}/parties`, 'POST', payload);

export const updateParty = (
  companyId: number,
  partyPk: number,
  payload: {
    name: string;
    orgnr?: string | null;
    address_line?: string | null;
    postal_code?: string | null;
    city?: string | null;
  },
): Promise<Party> =>
  json<Party>(`/api/v1/companies/${companyId}/parties/${partyPk}`, 'PATCH', payload);

// --- Cash flow ------------------------------------------------------------------

export type Direction = 'inn' | 'ut';

export interface CashFlowType {
  code: string;
  label: string;
  direction: Direction;
  account_number: string;
  default_tax_code: string;
  note: string;
  /**
   * The category this one reverses, when it is a refund or a kreditnota.
   *
   * A reversal does not book where its own direction suggests: a refund is
   * money in that goes back to the account the purchase came from, and a
   * kreditnota is money out that goes back to the account the sale came from.
   * Sixteen of the first and six of the second, so both are grouped apart in
   * the list rather than left to bury the ordinary ways money moves.
   */
  reverses: string | null;
  /** Only in a company's own catalogue: the type of the category's account. */
  account_type?: AccountType | null;
  /**
   * The add-on the category belongs to (`landbruk`), or null for the ones every
   * business has. Only an add-on the company has switched on reaches the page.
   */
  segment?: string | null;
}

export type AccountType = 'eiendel' | 'gjeld' | 'egenkapital' | 'inntekt' | 'kostnad';

export interface TaxCode {
  code: string;
  description: string;
  rate_percent: string;
}

export interface Catalogue {
  types: CashFlowType[];
  tax_codes: TaxCode[];
  max_attachment_bytes: number;
}

/**
 * The catalogue as one company's books see it today.
 *
 * The default VAT codes follow the company's registration status, only the codes
 * it may use are offered, and a category whose account has been deactivated is
 * left out. The entry form loads this one; the shared catalogue is for signup.
 */
export interface CompanyCatalogue extends Catalogue {
  vat_registered: boolean;
  vat_registered_from: string | null;
  /** Active accounts a category may be moved to, beside its own. */
  accounts: { number: string; description: string; account_type: AccountType }[];
  /** The add-ons these books have (`landbruk`), whose categories are in `types`. */
  segments: string[];
}

export interface AttachmentInfo {
  id: number;
  filename: string;
  /** Sniffed on upload, not taken from the browser. Decides the badge. */
  content_type: string;
  byte_size: number;
  sha256: string;
}

export interface CashFlowEntry {
  id: number;
  voucher_no: string;
  occurred_on: string;
  direction: Direction;
  type_code: string | null;
  type_label: string | null;
  gross_amount: string;
  net_amount: string;
  vat_amount: string;
  account_number: string;
  account_description: string;
  tax_code: string | null;
  party_id: number | null;
  party_name: string | null;
  payment_account: string;
  description: string;
  is_reversal: boolean;
  reversed_by_id: number | null;
  /**
   * The entry this one was posted to replace, when it came from an edit.
   *
   * An edit leaves three vouchers: the original, its reversal and this one.
   * The link is what lets the register show them as one correction rather than
   * as three unrelated payments, and it comes from the database rather than
   * from matching dates and amounts, which would eventually pair two genuine
   * payments made the same day.
   */
  corrects_id: number | null;
  /**
   * Who entered it, from the session that posted the voucher.
   *
   * `null` for an entry made before the backend recorded this, and for one
   * whose author has since been erased: the entry outlives the account, which
   * is bokføringsloven working as intended. Both cases read as «ukjent» — see
   * `lib/authors.ts`, which is also where the rule about *when* to show the
   * mark lives.
   *
   * The id comes along so two colleagues who share a name can still be told
   * apart, and so grouping never matches on a string a person typed.
   */
  created_by_id: number | null;
  created_by_name: string | null;
  /**
   * The key the author worked through, by the name its person gave it, when
   * the entry came from an agent or an integration rather than a page.
   * `null` for everything typed by hand. See docs/agents-and-api.md rule 6.
   */
  created_via: string | null;
  attachments: AttachmentInfo[];
  /**
   * The lines of a composite entry (`type_code` `sammensatt`): one payment,
   * several trades behind it. Empty for an ordinary entry. On a composite entry
   * `tax_code` is null, since each line has its own.
   */
  parts: CashFlowPart[];
  /**
   * The avdeling, prosjekt or driftsgren the entry is on, at most one value per
   * dimension (`listDimensions`). Empty on a composite entry, whose lines each
   * carry their own in `parts`.
   */
  dimension_value_ids: number[];
}

/** One line of a composite entry. */
export interface CashFlowPart {
  type_code: string | null;
  type_label: string | null;
  /** Which way this line moved money, which on a settlement differs per line. */
  direction: Direction;
  gross_amount: string;
  net_amount: string;
  vat_amount: string;
  account_number: string;
  account_description: string;
  tax_code: string | null;
  description: string;
  dimension_value_ids: number[];
}

/** The voucher type of a composite entry, as the register reads it back. */
export const COMPOSITE_TYPE = 'sammensatt';

/** What a composite entry is posted with. */
export interface CompositeEntry {
  occurred_on: string;
  description: string;
  payment_method: 'bank' | 'kontant';
  party_id?: number | null;
  lines: {
    type_code: string;
    gross_amount: string;
    tax_code?: string | null;
    account_number?: string | null;
    description?: string | null;
    /** Per line, because one settlement can be several driftsgrener. */
    dimension_value_ids?: number[];
  }[];
}

export interface TypeTotal {
  type_code: string | null;
  type_label: string | null;
  direction: Direction;
  total: string;
  count: number;
}

export interface Summary {
  financial_year: number;
  total_in: string;
  total_out: string;
  net: string;
  entry_count: number;
  /** The year's VAT on sales and purchases, settlements of filed terms left out. */
  output_vat: string;
  input_vat: string;
  /**
   * What is owed now, across every year: open terms plus filed terms not yet
   * paid (2700, 2710 and 2740). Positive is owed to Skatteetaten.
   */
  vat_position: string;
  /** The part of `vat_position` filed and settled onto 2740, its payment not yet posted. */
  vat_settled: string;
  /** What the bank accounts and the cash box hold, across every year. */
  cash_position: string;
  /** The date of the last voucher in the books, any year, or null when nothing is posted. */
  books_through: string | null;
  /**
   * `books_through` when the VAT deadline of the period the books stop in has
   * passed with nothing posted since: `vat_position` is that day's, not
   * today's. Null while the books are current, including on 1 January.
   */
  positions_as_of: string | null;
  by_type: TypeTotal[];
}

/** One account behind a line of the accounts. */
export interface ReportAccount {
  account_number: string;
  description: string;
  amount: string;
}

/** One line of a resultatregnskap or balanse; `key` is the same in every year. */
export interface ReportLine {
  key: string;
  label: string;
  /** `gruppe` sums accounts, listed in `accounts`; `sum` adds up lines above. */
  kind: 'gruppe' | 'sum';
  amount: string;
  accounts: ReportAccount[];
}

export interface ReportYear {
  year: number;
  start_date: string;
  end_date: string;
  closed: boolean;
  voucher_count: number;
  /** The date of the year's last voucher; null when nothing is posted in it. */
  last_entry: string | null;
  resultat: ReportLine[];
  balanse: ReportLine[];
}

export interface Accounts {
  company_id: number;
  /** Newest first. */
  years: ReportYear[];
}

/** Where a proposed category came from. See /ki. */
/** Where one proposed field came from: the text of the bilag, or Løynd. */
/**
 * Where a proposed value came from.
 *
 * `beregnet` is neither of the other two: it is arithmetic on something read,
 * and the only thing that produces it is a foreign total multiplied by a
 * published Norges Bank rate. Shown differently because the user can check it
 * against a rate table, which is true of no other proposal.
 */
export type FieldSource = 'dokument' | 'ki' | 'beregnet';

/**
 * What an uploaded document appears to say, as form values.
 *
 * Everything is nullable: the backend proposes only what it actually found, so
 * a null means "we could not tell", never "zero" or "none". `sources` names the
 * fields that were proposed and where each one came from, which is what lets the
 * form mark them as read rather than typed, and a reading apart from a guess.
 */
export interface InvoiceSuggestion {
  direction: Direction;
  type_code: string | null;
  type_label: string | null;
  type_source: FieldSource | null;
  tax_code: string | null;
  occurred_on: string | null;
  gross_amount: string | null;
  vat_amount: string | null;
  description: string | null;
  party_id: number | null;
  counterparty_name: string | null;
  counterparty_orgnr: string | null;
  invoice_number: string | null;
  currency_code: string | null;
  /**
   * The total as the bilag prints it, when that is not in kroner.
   *
   * `gross_amount` is null in that case on purpose: the ledger is in kroner, and
   * what the account was charged is on the bank statement rather than on the
   * document. This is the number to show the user while they look it up.
   */
  foreign_amount: string | null;
  /**
   * Norges Bank's rate, and the day it is quoted for, when `gross_amount` was
   * worked out from `foreign_amount` instead of read off the page. Both are
   * shown next to the amount so the multiplication can be repeated by hand.
   */
  exchange_rate: string | null;
  rate_quoted_on: string | null;
  filled: string[];
  sources: Record<string, FieldSource>;
  warnings: string[];
  /** Whether a model was actually consulted. False when KI is off, unconfigured, or down. */
  ki_used: boolean;
  /**
   * What the register still needs to know. Answered with `answerSuggestion`,
   * which comes back with a better proposal.
   */
  questions: SuggestionQuestion[];
  /**
   * Where the answers go: `answerSuggestion` continues from this reading
   * instead of reading the files again. Null when no model was asked, and then
   * the bilag is simply read again.
   */
  reading_id: string | null;
  /** One row per uploaded file, in upload order. */
  documents_read: DocumentRead[];
  /**
   * Whether anything besides the documents was put in front of the model: the
   * company's registered formål and aktivitet, and the sentence the user typed.
   */
  context_used: boolean;
}

/** Something the documents did not settle. */
export interface SuggestionQuestion {
  text: string;
  /** The form field the answer would fill, when it maps to one. */
  field: string | null;
  /** True for the one question the model composed rather than the register. */
  asked_by_model: boolean;
  /**
   * Which question this is (`belop`, `innbetaling`, `kategori_tvil`,
   * `kategori`, `mva_satser`, `driftsmiddel`, or `ki` for the model's own).
   * Sent back with the answer; an answered key is not asked again.
   */
  key: string;
  /** The answers it can be given with one press. Empty for an open question. */
  options: SuggestionOption[];
}

/** One answer to a question, given with a press. */
export interface SuggestionOption {
  /** What the button says, and what is sent back as the answer. */
  label: string;
  /** The category this answer means, when it means one. */
  type_code: string | null;
  /** The VAT code this answer means, when it means one. */
  tax_code: string | null;
  /** The payment is several things: one line each, in a composite entry. */
  split: boolean;
}

/** What the person said to one question. */
export interface SuggestionAnswer {
  key: string | null;
  question: string;
  answer: string;
}

/** One uploaded file, and how much text came out of it. */
export interface DocumentRead {
  filename: string;
  content_type: string;
  /** Zero means nothing was read from this one — a blurred photo, a blank page. */
  chars: number;
}

export const getCatalogue = (): Promise<Catalogue> => request<Catalogue>('/api/v1/cashflow/types');

export const getCompanyCatalogue = (companyId: number): Promise<CompanyCatalogue> =>
  request<CompanyCatalogue>(`/api/v1/companies/${companyId}/cashflow/types`);

/**
 * Read the uploaded bilag and get the entry form filled in. Stores nothing.
 *
 * Takes a list because a bilag is often not one file. A purchase from a private
 * seller on FINN is documented by the advert, the chat where the price was
 * agreed, the receipt and the line on the bank statement, and no one of the four
 * is the bilag — together they are. There is deliberately no limit on how many.
 *
 * `forklaring` is the sentence the user typed about the payment, for the bilag
 * the documents cannot settle on their own: three thousand kroner arriving from
 * a private account is a deposit from the owner, a repaid loan or a sale, and
 * the paperwork looks identical in all three cases. This request only shows it to
 * the model; it is the form's description field, so registering the payment is
 * what stores and posts it, as the bilagstekst.
 */
/**
 * Read a bilag and propose the entry, in one JSON answer.
 *
 * The register's own page uses `interpretDocumentStreaming` below, because the
 * wait is worth showing. This is the same proposal without the frames, kept
 * because the route is: a caller that only wants the answer should not have to
 * parse a stream to get it.
 */
export const interpretDocument = (
  companyId: number,
  files: File[],
  forklaring?: string,
  retning?: Direction,
): Promise<InvoiceSuggestion> => {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  if (forklaring !== undefined && forklaring.trim() !== '') {
    form.append('forklaring', forklaring.trim());
  }
  if (retning !== undefined) form.append('retning', retning);
  return request<InvoiceSuggestion>(`/api/v1/companies/${companyId}/cashflow/forslag`, {
    method: 'POST',
    body: form,
  });
};

/**
 * Answer the questions on a proposal, and get the proposal again.
 *
 * Nothing is uploaded: the reading `reading_id` names is kept on the server for
 * ten minutes after its last use, and the model is asked a follow-up to the
 * conversation it already had about the bilag. `answers` is every answer given
 * on this bilag so far, not only the newest, so a reading that is gone can be
 * replaced by reading the files again and sending the same list. That is what
 * an `ApiError` with code `reading_not_found` asks for.
 *
 * `forklaring` is the description as it stands, sent only when it is the
 * person's own sentence, as for `interpretDocument`.
 */
export const answerSuggestion = (
  companyId: number,
  readingId: string,
  answers: SuggestionAnswer[],
  forklaring?: string,
): Promise<InvoiceSuggestion> =>
  json<InvoiceSuggestion>(
    `/api/v1/companies/${companyId}/cashflow/forslag/${encodeURIComponent(readingId)}/svar`,
    'POST',
    {
      answers,
      forklaring: forklaring !== undefined && forklaring.trim() !== '' ? forklaring.trim() : null,
    },
  );

/** What the streaming reader reports on its way to a proposal. */
export interface InterpretHandlers {
  /** Which files were read, and how much text came out of each. Arrives first. */
  onDocuments?: (documents: DocumentRead[]) => void;
  /**
   * Something worth saying while the user waits: so far only that the bilag is
   * long and the model will take longer over it. At most once, before the
   * description starts.
   */
  onNotice?: (message: string) => void;
  /** The next piece of the description, as the model writes it. */
  onDescription?: (piece: string) => void;
  /**
   * Which way the money went, when the user has said so. It decides the
   * direction and narrows the categories, and the model is told it.
   */
  retning?: Direction;
}

/**
 * The same proposal as `interpretDocument`, reported as it is worked out.
 *
 * Reading a bilag is not a uniform wait: a PDF's text layer is milliseconds,
 * and a photographed receipt is a vision model looking at a page — nine
 * screenshots take about a minute and a half. Spending that on a spinner and
 * then filling the whole form at once is the worst arrangement of the same
 * time. So the file list arrives as soon as the text does, and the description
 * is typed out as it is written.
 *
 * Resolves with the finished suggestion, which is exactly what the plain route
 * returns — a caller that passes no handlers has simply chosen not to watch.
 * Anything the server can refuse it refuses before the stream opens, so a bad
 * upload is still an `ApiError` with a status rather than a frame.
 */
export const interpretDocumentStreaming = async (
  companyId: number,
  files: File[],
  forklaring: string | undefined,
  handlers: InterpretHandlers = {},
): Promise<InvoiceSuggestion> => {
  const path = `/api/v1/companies/${companyId}/cashflow/forslag/strom`;
  // The same guard the plain path applies, and for the same reason: a request
  // for the wrong company must not be sent, let alone have its answer painted.
  const scope = companyInPath(path);
  const active = activeCompanyId();
  if (scope !== null && active !== null && scope !== active) {
    throw new WrongCompanyError(
      'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
      active,
      scope,
    );
  }

  const form = new FormData();
  for (const file of files) form.append('files', file);
  if (forklaring !== undefined && forklaring.trim() !== '') {
    form.append('forklaring', forklaring.trim());
  }
  if (handlers.retning !== undefined) form.append('retning', handlers.retning);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      credentials: 'include',
      body: form,
    });
  } catch {
    throw new ApiError(0, 'Fikk ikke kontakt med serveren.', 'network');
  }

  if (!response.ok) {
    const text = await response.text();
    const body: unknown = text ? JSON.parse(text) : null;
    const code =
      typeof body === 'object' && body !== null && typeof (body as { code?: unknown }).code === 'string'
        ? (body as { code: string }).code
        : null;
    throw new ApiError(response.status, messageFrom(response.status, body), code);
  }
  if (response.body === null) {
    throw new ApiError(0, 'Fikk ikke lest svaret fra serveren.', 'network');
  }

  let suggestion: InvoiceSuggestion | null = null;
  let failure: string | null = null;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const handle = (frame: string): void => {
    let name = '';
    const data: string[] = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('event: ')) name = line.slice('event: '.length);
      // Only ever one data line per frame here, but the format allows several
      // and joining them with a newline is what the format says to do.
      else if (line.startsWith('data: ')) data.push(line.slice('data: '.length));
    }
    if (data.length === 0) return;
    let payload: unknown;
    try {
      payload = JSON.parse(data.join('\n'));
    } catch {
      // A frame we cannot read costs nothing. The proposal arrives in the last
      // one, and that is the only frame anything depends on.
      return;
    }
    if (name === 'dokumenter') handlers.onDocuments?.(payload as DocumentRead[]);
    else if (name === 'advarsel') handlers.onNotice?.((payload as { melding: string }).melding);
    else if (name === 'beskrivelse') handlers.onDescription?.((payload as { tekst: string }).tekst);
    else if (name === 'forslag') suggestion = payload as InvoiceSuggestion;
    else if (name === 'feil') failure = (payload as { melding: string }).melding;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Frames are separated by a blank line; a partial one stays in the buffer.
    let split = buffer.indexOf('\n\n');
    while (split !== -1) {
      handle(buffer.slice(0, split));
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim() !== '') handle(buffer);

  if (failure !== null) throw new ApiError(500, failure, 'stream');
  if (suggestion === null) {
    // The stream ended without a proposal: the connection dropped, or the
    // server died mid-answer. Either way there is nothing to fill the form in
    // with, and saying so is better than leaving a half-painted form.
    throw new ApiError(0, 'Lesingen ble avbrutt før forslaget var ferdig.', 'network');
  }
  if (scope !== null) assertCompanyScope(scope, suggestion);
  return suggestion;
};

export const listCashFlow = (companyId: number, limit = 100): Promise<CashFlowEntry[]> =>
  request<CashFlowEntry[]>(`/api/v1/companies/${companyId}/cashflow?limit=${limit}`);

export const getSummary = (companyId: number, financialYear?: number): Promise<Summary> =>
  request<Summary>(
    `/api/v1/companies/${companyId}/cashflow/summary` +
      (financialYear === undefined ? '' : `?financial_year=${financialYear}`),
  );

/** Resultat og balanse for every financial year, newest first. */
export const getAccounts = (companyId: number): Promise<Accounts> =>
  request<Accounts>(`/api/v1/companies/${companyId}/regnskap`);

export const recordCashFlow = (
  companyId: number,
  payload: {
    type_code: string;
    occurred_on: string;
    gross_amount: string;
    description: string;
    tax_code?: string | null;
    payment_method: 'bank' | 'kontant';
    party_id?: number | null;
    account_number?: string | null;
    dimension_value_ids?: number[];
  },
): Promise<CashFlowEntry> =>
  json<CashFlowEntry>(`/api/v1/companies/${companyId}/cashflow`, 'POST', payload);

/**
 * Change a registered payment.
 *
 * A posted voucher cannot be altered, so the backend reverses the original and
 * posts the corrected version, both in one transaction. The register ends up
 * showing three entries; the user sees one edit. Returns the corrected entry.
 */
/** Post one payment that is several trades: a settlement, a payout less fees. */
export const recordComposite = (
  companyId: number,
  payload: CompositeEntry,
): Promise<CashFlowEntry> =>
  json<CashFlowEntry>(`/api/v1/companies/${companyId}/cashflow/sammensatt`, 'POST', payload);

/**
 * Replace a posted entry, ordinary or composite, with a composite one: the
 * original is reversed and the replacement posted in the same request, as for
 * `editCashFlow`.
 */
export const editComposite = (
  companyId: number,
  voucherId: number,
  payload: CompositeEntry,
): Promise<CashFlowEntry> =>
  json<CashFlowEntry>(
    `/api/v1/companies/${companyId}/cashflow/sammensatt/${voucherId}`,
    'PUT',
    payload,
  );

export const editCashFlow = (
  companyId: number,
  voucherId: number,
  payload: {
    type_code: string;
    occurred_on: string;
    gross_amount: string;
    description: string;
    tax_code?: string | null;
    payment_method: 'bank' | 'kontant';
    party_id?: number | null;
    account_number?: string | null;
    dimension_value_ids?: number[];
  },
): Promise<CashFlowEntry> =>
  json<CashFlowEntry>(`/api/v1/companies/${companyId}/cashflow/${voucherId}`, 'PUT', payload);

export const reverseCashFlow = (companyId: number, voucherId: number): Promise<CashFlowEntry> =>
  request<CashFlowEntry>(`/api/v1/companies/${companyId}/cashflow/${voucherId}/reversal`, {
    method: 'POST',
  });

export const uploadDocument = (
  companyId: number,
  voucherId: number,
  file: File,
): Promise<AttachmentInfo> => {
  const form = new FormData();
  form.append('file', file);
  // No Content-Type header: the browser must set the multipart boundary itself.
  return request<AttachmentInfo>(`/api/v1/companies/${companyId}/cashflow/${voucherId}/dokument`, {
    method: 'POST',
    body: form,
  });
};

export const documentUrl = (companyId: number, attachmentId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/dokument/${attachmentId}`;

/**
 * The thumbnail of a photographed bilag, or null when there is none.
 *
 * Fetched rather than set as an `<img src>`, because an image tag is a request
 * the company wall never sees: a stale id in a listing would paint another
 * company's receipt under this company's name. So the same outward check as
 * `request()` runs first, and the picture arrives as a blob the page turns into
 * an object URL.
 *
 * Null for a 404, which is the ordinary answer for a PDF (no thumbnail is made
 * of one) and for a photo no decoder would open. The page shows the badge it
 * always showed. Any other failure is null too: a thumbnail is decoration, and
 * a register that fails to list because a picture did not load is worse.
 */
export const fetchThumbnail = async (
  companyId: number,
  attachmentId: number,
): Promise<Blob | null> => {
  const path = `/api/v1/companies/${companyId}/dokument/${attachmentId}/miniatyr`;
  const scope = companyInPath(path);
  const active = activeCompanyId();
  if (scope !== null && active !== null && scope !== active) {
    throw new WrongCompanyError(
      'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
      active,
      scope,
    );
  }
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' });
    return response.ok ? await response.blob() : null;
  } catch {
    return null;
  }
};

// --- Faktura ---------------------------------------------------------------------
//
// The lifecycle, not CRUD: a draft is free to edit, issuing is one irreversible
// call, and after it the only route is /kreditnota. That shape comes from
// bokføringsforskriften § 5-2-7, not from a preference about REST.

export type DocumentType = 'faktura' | 'kreditnota';
export type InvoiceStatus = 'utkast' | 'utstedt';

/** Whether a finding stops the document from being issued, or is only worth knowing. */
export type Severity = 'blokkerende' | 'advarsel';

export interface Violation {
  /** The legal reference, e.g. "bokføringsforskriften § 5-1-1 nr. 4". */
  rule: string;
  /** Which field to go and fill in, so the form can point at it. */
  field: string;
  message: string;
  severity: Severity;
}

export interface SalesTaxOption {
  tax_code: string;
  label: string;
  income_account: string;
  note: string;
  rate_percent: string;
}

export interface Unit {
  code: string;
  label: string;
}

export interface FakturaCatalogue {
  tax_options: SalesTaxOption[];
  units: Unit[];
  default_unit: string;
  default_payment_term_days: number;
  /** When e-invoicing to a business customer stops being advisory. */
  e_invoice_mandatory_from: string;
  /**
   * Whether this deployment has a model configured at all.
   *
   * False means the deployment has opted out (no `LOYND_KEY`), not that
   * something is broken, so the form leaves the box out rather than offering a
   * button that always fails.
   */
  ai_available: boolean;
  /** The longest description `draftFakturaLines` accepts. */
  ai_max_chars: number;
}

export interface InvoiceLine {
  line_no: number;
  description: string;
  quantity: string;
  unit_code: string;
  unit_price: string;
  discount_percent: string;
  tax_code: string;
  tax_rate_percent: string;
  net_amount: string;
  vat_amount: string;
}

/** One § 5-1-5 group: taxable, zero-rated and exempt each summed for itself. */
export interface TaxSubtotal {
  category: string;
  rate_percent: string;
  reason: string | null;
  taxable_amount: string;
  tax_amount: string;
}

export interface Faktura {
  id: number;
  document_type: DocumentType;
  status: InvoiceStatus;
  /** Null until issued. Numbering a draft is how a series acquires gaps. */
  document_number: string | null;

  party_id: number | null;
  buyer_name: string;
  buyer_orgnr: string | null;
  buyer_address_line: string | null;
  buyer_postal_code: string | null;
  buyer_city: string | null;
  buyer_is_business: boolean;

  seller_name: string;
  seller_orgnr: string;
  seller_vat_registered: boolean;
  seller_in_foretaksregisteret: boolean;
  /** What the document *says*, snapshotted at issue. Eleven digits, or null. */
  seller_bank_account: string | null;
  /**
   * Which of the company's accounts this document is paid into.
   *
   * Null means "whichever is primary", which is the answer for a company with
   * one account and for a draft nobody has picked for. On an issued document it
   * can also mean the account has since been closed — the number above is what
   * the customer was told, and it does not move.
   */
  bank_account_id: number | null;

  issue_date: string | null;
  delivery_date: string | null;
  delivery_place: string | null;
  due_date: string | null;
  currency_code: string;
  buyer_reference: string | null;
  order_reference: string | null;
  /** The KID. Null on a draft even when one is coming, since it carries the invoice number. */
  payment_reference: string | null;
  /** Whether issuing assigns that KID from the invoice number. */
  payment_reference_auto: boolean;
  note: string | null;
  reverse_charge: boolean;

  lines: InvoiceLine[];
  subtotals: TaxSubtotal[];
  net_total: string;
  vat_total: string;
  gross_total: string;

  /** The sentences the document itself carries, so the UI shows the same text. */
  markings: string[];
  violations: Violation[];

  voucher_id: number | null;
  credits_document_id: number | null;
  credited_by_document_id: number | null;
  has_ehf: boolean;
}

export interface LinePayload {
  description: string;
  quantity: string;
  unit_price: string;
  tax_code: string;
  unit_code?: string;
  discount_percent?: string;
}

export interface FakturaPayload {
  party_id?: number | null;
  buyer_name?: string | null;
  buyer_orgnr?: string | null;
  buyer_address_line?: string | null;
  buyer_postal_code?: string | null;
  buyer_city?: string | null;
  buyer_is_business?: boolean;
  delivery_date?: string | null;
  delivery_place?: string | null;
  due_date?: string | null;
  buyer_reference?: string | null;
  order_reference?: string | null;
  /**
   * A KID to use. The empty string clears it, and `null` means "leave it alone"
   * throughout this payload, so it could not otherwise be removed once set.
   * Sending one turns `payment_reference_auto` off.
   */
  payment_reference?: string | null;
  payment_reference_auto?: boolean;
  /**
   * Which account to be paid into. Omitted leaves it alone, and a draft that
   * has never named one follows the company's primary — so the form only has to
   * send this where there is more than one account to choose between.
   */
  bank_account_id?: number | null;
  note?: string | null;
  reverse_charge?: boolean;
  lines?: LinePayload[];
}

/** One line a model proposed, in the same shape `LinePayload` takes back. */
export interface DraftedLine {
  description: string;
  quantity: string;
  unit_code: string;
  unit_price: string;
  tax_code: string;
}

export interface LineDraft {
  /**
   * Empty whenever nothing usable came back: no key, no answer, or an answer
   * we could not read. All three are ordinary outcomes, and `warnings` says
   * which one happened.
   */
  lines: DraftedLine[];
  warnings: string[];
}

/** The number and KID the next issued invoice would get. Reserves nothing. */
export interface NextNumber {
  document_number: string;
  /** kundenummer + fakturanummer + kontrollsiffer. */
  payment_reference: string;
  /** The customer number in it, or null when the buyer is not a registered customer. */
  customer_number: string | null;
}

/**
 * Preview the next invoice number and its KID.
 *
 * A preview and not a reservation: an invoice issued in between moves the
 * series on, so the interface labels it rather than presenting it as settled.
 *
 * `partyId` is the customer the invoice is going to, and the KID carries their
 * customer number, so the preview changes when the customer does.
 */
export const getNextFakturaNumber = (
  companyId: number,
  partyId?: number | null,
): Promise<NextNumber> =>
  request<NextNumber>(
    `/api/v1/companies/${companyId}/faktura/neste-nummer` +
      (partyId === undefined || partyId === null ? '' : `?party_id=${partyId}`),
  );

export const getFakturaCatalogue = (): Promise<FakturaCatalogue> =>
  request<FakturaCatalogue>('/api/v1/faktura/katalog');

/**
 * Turn a sentence about a sale into proposed invoice lines. Stores nothing.
 *
 * The proposal goes into the form, where the user edits it and saves it like
 * any line they typed. See /ki for what the model is and is not asked.
 */
export const draftFakturaLines = (companyId: number, text: string): Promise<LineDraft> =>
  json<LineDraft>(`/api/v1/companies/${companyId}/faktura/linjeforslag`, 'POST', { text });

export const listFaktura = (companyId: number, limit = 100): Promise<Faktura[]> =>
  request<Faktura[]>(`/api/v1/companies/${companyId}/faktura?limit=${limit}`);

export const getFaktura = (companyId: number, documentId: number): Promise<Faktura> =>
  request<Faktura>(`/api/v1/companies/${companyId}/faktura/${documentId}`);

export const createFaktura = (companyId: number, payload: FakturaPayload): Promise<Faktura> =>
  json<Faktura>(`/api/v1/companies/${companyId}/faktura`, 'POST', payload);

export const updateFaktura = (
  companyId: number,
  documentId: number,
  payload: FakturaPayload,
): Promise<Faktura> =>
  json<Faktura>(`/api/v1/companies/${companyId}/faktura/${documentId}`, 'PATCH', payload);

export const deleteFaktura = (companyId: number, documentId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/faktura/${documentId}`, {
    method: 'DELETE',
  });

/**
 * What still stands between this draft and a lawful invoice.
 *
 * Safe to call as often as the form likes: it reads, and it runs exactly the
 * checks issuing will run.
 */
export const checkFaktura = (companyId: number, documentId: number): Promise<Violation[]> =>
  request<Violation[]>(`/api/v1/companies/${companyId}/faktura/${documentId}/kontroll`);

/** Irreversible: numbers the document, posts it, freezes its EHF, files its PDF. */
export const issueFaktura = (companyId: number, documentId: number): Promise<Faktura> =>
  request<Faktura>(`/api/v1/companies/${companyId}/faktura/${documentId}/utsted`, {
    method: 'POST',
  });

export const creditFaktura = (
  companyId: number,
  documentId: number,
  note?: string | null,
): Promise<Faktura> =>
  json<Faktura>(`/api/v1/companies/${companyId}/faktura/${documentId}/kreditnota`, 'POST', {
    note: note ?? null,
  });

export const fakturaPdfUrl = (companyId: number, documentId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/faktura/${documentId}/pdf`;

/**
 * An invoice's PDF as a blob, for showing inside the page, or null.
 *
 * `fakturaPdfUrl` is a download: the API answers it with `Content-Disposition:
 * attachment` and, like every API answer, `X-Frame-Options: DENY`, so the page
 * cannot frame it from the API's origin. Fetched instead and handed back for
 * an object URL, which a frame on the page's own origin may show. The outward
 * check from `request()` runs first, for the reason `fetchThumbnail` gives.
 *
 * Null unless the answer says it is a PDF. An object URL takes the page's
 * origin, so anything else framed through one, an HTML error page from a proxy
 * say, would run as myntos.no. Null for any other failure too: the preview is
 * a convenience, and the download link beside it still works.
 */
export const fetchFakturaPdf = async (
  companyId: number,
  documentId: number,
): Promise<Blob | null> => {
  const path = `/api/v1/companies/${companyId}/faktura/${documentId}/pdf`;
  const active = activeCompanyId();
  if (active !== null && companyId !== active) {
    throw new WrongCompanyError(
      'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
      active,
      companyId,
    );
  }
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include' });
    const type = response.headers.get('Content-Type') ?? '';
    if (!response.ok || !type.startsWith('application/pdf')) return null;
    return await response.blob();
  } catch {
    return null;
  }
};

export const fakturaEhfUrl = (companyId: number, documentId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/faktura/${documentId}/ehf`;

// --- MVA-melding -------------------------------------------------------------------

/** One line of the melding: a code, its side, and what it comes to. */
export interface MvaLine {
  mva_kode: string;
  description: string;
  /** A reverse-charge code appears twice, once on each side. */
  side: 'utgaaende' | 'inngaaende';
  /** Absent on a deduction: Skatteetaten wants only the amount there. */
  grunnlag: string | null;
  sats_percent: string | null;
  merverdiavgift: string;
  spesifikasjon: string | null;
}

export interface MvaBlocker {
  code: string;
  message: string;
}

/** One finding from Skatteetaten's validation, in their words. */
export interface MvaFinding {
  sti: string;
  melding: string;
  alvorlighet: string | null;
  kode: string | null;
  mva_kode: string | null;
  detaljer: string | null;
}

/**
 * `annet_program` is not a filing: the person recorded that another program
 * filed the termin. `opphevet` is such a statement taken back.
 */
export type MvaFilingStatus =
  | 'validert'
  | 'avvist'
  | 'sendt'
  | 'mottatt'
  | 'feilet'
  | 'annet_program'
  | 'opphevet';

export interface MvaDocument {
  data_type: string;
  filename: string;
  content_type: string;
  byte_size: number;
}

/** A melding as it was checked, and what happened to it after. */
export interface MvaFiling {
  id: number;
  company_id: number;
  year: number;
  termin: number;
  period_label: string;
  status: MvaFilingStatus;
  fastsatt_merverdiavgift: string;
  lines: MvaLine[];
  merknad: string | null;
  validation_status: string;
  /** Skatteetaten would refuse it, or the answer could not be read. */
  validation_blocks: boolean;
  findings: MvaFinding[];
  validated_at: string;
  submitted_at: string | null;
  submitted_by_name: string | null;
  altinn_instance_id: string | null;
  feedback_received_at: string | null;
  error: string | null;
  payment_kid: string | null;
  payment_amount: string | null;
  payment_due: string | null;
  payment_account: string | null;
  settlement_voucher_id: number | null;
  documents: MvaDocument[];
  /** On the period page: whether the ledger still says what this filing says. */
  matches_ledger: boolean | null;
  /** Which melding: `alminnelig` or `primaernaering`. */
  category: string;
  /** Set on a termin recorded as filed from another program. */
  elsewhere: MvaElsewhere | null;
}

/** «Sendt fra et annet program»: the person's statement, not a filing. */
export interface MvaElsewhere {
  filed_on: string;
  /** What the other program filed, when the person said. */
  amount: string | null;
  voucher_id: number | null;
  withdrawn_at: string | null;
  /** Myntos's figure minus `amount`. */
  difference: string | null;
  /** Within a krone: some programs round the settlement to whole kroner. */
  agrees: boolean | null;
}

/** An imported settlement voucher that looks like the old program filed the termin. */
export interface MvaElsewhereProposal {
  voucher_id: number;
  voucher_no: string;
  date: string;
  description: string;
  output_vat: string;
  input_vat: string;
  amount: string;
  /** Output and input exactly Myntos's. */
  matches: boolean;
}

export interface MvaPeriodRow {
  year: number;
  termin: number;
  label: string;
  name: string;
  start: string;
  end: string;
  deadline: string;
  is_over: boolean;
  fastsatt_merverdiavgift: string;
  line_count: number;
  status: MvaFilingStatus | null;
  filed_fastsatt: string | null;
  /** Filed, and the ledger for the period has moved since. */
  changed_since_filed: boolean;
  /** Past the deadline with nothing filed or recorded as filed elsewhere. */
  is_overdue: boolean;
  filed_elsewhere: boolean;
  filed_elsewhere_amount: string | null;
  elsewhere_difference: string | null;
  elsewhere_proposal: MvaElsewhereProposal | null;
}

export interface MvaChoice {
  value: string;
  label: string;
}

/** The second melding of a company that files two (a farm with other business). */
export interface MvaSecondMelding {
  category: string;
  label: string;
  period_type: string;
  valid_from: string;
  accounts: { account_number: string; name: string }[];
}

export interface MvaSettings {
  period_type: string;
  category: string;
  period_types: MvaChoice[];
  categories: MvaChoice[];
  second: MvaSecondMelding | null;
}

/** One of the company's meldinger. Almost every company has one. */
export interface MvaNaering {
  category: string;
  label: string;
  period_type: string;
  is_main: boolean;
  valid_from: string | null;
}

export interface MvaOverview {
  company_id: number;
  year: number;
  vat_registered: boolean;
  settings: MvaSettings;
  /** Which melding `periods` are for. */
  naering: string;
  naeringer: MvaNaering[];
  periods: MvaPeriodRow[];
}

export interface MvaPeriod {
  company_id: number;
  year: number;
  termin: number;
  label: string;
  name: string;
  start: string;
  end: string;
  deadline: string;
  category: string;
  orgnr: string;
  lines: MvaLine[];
  output_vat: string;
  input_vat: string;
  fastsatt_merverdiavgift: string;
  reconciliation: { output_account: string; input_account: string; difference: string };
  /** What the backend says stands in the way. The page shows these, not its own. */
  blockers: MvaBlocker[];
  /** False when this installation cannot check or file; offer the XML instead. */
  submission_available: boolean;
  changed_since_filed: boolean;
  is_overdue: boolean;
  /** The standing «Sendt fra et annet program» statement, if any. */
  filed_elsewhere: MvaFiling | null;
  elsewhere_proposal: MvaElsewhereProposal | null;
  filings: MvaFiling[];
}

/** `&naering=…` for the second melding; nothing for the main one. */
const naeringQuery = (naering?: string | null): string =>
  naering ? `&naering=${encodeURIComponent(naering)}` : '';

export const getMvaOverview = (
  companyId: number,
  year: number,
  naering?: string | null,
): Promise<MvaOverview> =>
  request<MvaOverview>(`/api/v1/companies/${companyId}/mva?aar=${year}${naeringQuery(naering)}`);

export const getMvaPeriod = (
  companyId: number,
  year: number,
  termin: number,
  naering?: string | null,
): Promise<MvaPeriod> =>
  request<MvaPeriod>(
    `/api/v1/companies/${companyId}/mva/termin?aar=${year}&termin=${termin}${naeringQuery(naering)}`,
  );

/** Set up or change the second melding. `accounts` replaces the list. */
export const setSecondMvaMelding = (
  companyId: number,
  payload: { period_type: string; valid_from: string; accounts: string[] },
): Promise<MvaSettings> =>
  json<MvaSettings>(`/api/v1/companies/${companyId}/mva/andre-melding`, 'PUT', payload);

/** Back to one melding; refused once the second has anything filed. */
export const removeSecondMvaMelding = (companyId: number): Promise<MvaSettings> =>
  request<MvaSettings>(`/api/v1/companies/${companyId}/mva/andre-melding`, { method: 'DELETE' });

/** Record that another program filed the termin. Sends nothing to Skatteetaten. */
export const markMvaFiledElsewhere = (
  companyId: number,
  payload: {
    aar: number;
    termin: number;
    naering?: string | null;
    filed_on: string;
    amount?: string | null;
    voucher_id?: number | null;
  },
): Promise<MvaFiling> =>
  json<MvaFiling>(`/api/v1/companies/${companyId}/mva/termin/annet-program`, 'POST', payload);

/** Take a «Sendt fra et annet program» statement back. */
export const withdrawMvaFiledElsewhere = (companyId: number, filingId: number): Promise<MvaFiling> =>
  request<MvaFiling>(`/api/v1/companies/${companyId}/mva/innsendinger/${filingId}/angre`, {
    method: 'POST',
  });

export const updateMvaSettings = (
  companyId: number,
  payload: { period_type?: string; category?: string },
): Promise<MvaSettings> =>
  json<MvaSettings>(`/api/v1/companies/${companyId}/mva/innstillinger`, 'PATCH', payload);

/**
 * Send the melding to Skatteetaten's validation. Files nothing.
 *
 * Needs the ID-porten login; rejects with 409 when there is none.
 */
export const checkMvaPeriod = (
  companyId: number,
  year: number,
  termin: number,
  merknad: string | null,
  naering?: string | null,
): Promise<MvaFiling> =>
  json<MvaFiling>(`/api/v1/companies/${companyId}/mva/termin/kontroll`, 'POST', {
    aar: year,
    termin,
    merknad,
    naering: naering || null,
  });

/**
 * Irreversible: files the checked melding with Skatteetaten through Altinn.
 *
 * `fastsatt` is the figure the person confirmed. The backend refuses unless it
 * is exactly the checked one, and refuses (`melding_changed`) if the ledger has
 * moved since the check.
 */
export const submitMvaFiling = (
  companyId: number,
  filingId: number,
  fastsatt: string,
): Promise<MvaFiling> =>
  json<MvaFiling>(`/api/v1/companies/${companyId}/mva/innsendinger/${filingId}/send`, 'POST', {
    fastsatt_merverdiavgift: fastsatt,
    bekreftet: true,
  });

/** Fetch the receipt and payment information that arrived after filing. */
export const fetchMvaFeedback = (companyId: number, filingId: number): Promise<MvaFiling> =>
  request<MvaFiling>(`/api/v1/companies/${companyId}/mva/innsendinger/${filingId}/tilbakemelding`, {
    method: 'POST',
  });

export const mvaPeriodXmlUrl = (
  companyId: number,
  year: number,
  termin: number,
  naering?: string | null,
): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/mva/termin/xml?aar=${year}&termin=${termin}${naeringQuery(naering)}`;

export const mvaFiledXmlUrl = (companyId: number, filingId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/mva/innsendinger/${filingId}/melding.xml`;

export const mvaDocumentUrl = (companyId: number, filingId: number, dataType: string): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/mva/innsendinger/${filingId}/dokument?type=${encodeURIComponent(dataType)}`;

// --- Skattemelding ---------------------------------------------------------------
//
// An aksjeselskap's skattemelding and næringsspesifikasjon for one income year,
// built from a closed year's books. Checked with Skatteetaten on the person's
// ID-porten login, then sent, as the MVA-melding is. See
// backend/src/myntos/api/v1/skattemelding.py.

/** `feil` stops the check; `merknad` is for reading. */
export interface SkattemeldingFinding {
  severity: 'feil' | 'merknad';
  code: string;
  message: string;
}

export interface SkattemeldingPost {
  category: string;
  code: string;
  name: string;
  /** As sent: the books' own side, turned round where Skatteetaten subtracts. */
  amount: string;
  subtracted: boolean;
  accounts: string[];
}

export interface SkattemeldingSaldo {
  key: string;
  saldogruppe: string;
  label: string;
  opening: string;
  additions: string;
  grants: string;
  disposals: string;
  basis: string;
  depreciation: string;
  income: string;
  closing: string;
  /** What is sent: the depreciation as a percentage of the basis. */
  rate_percent: string;
}

export interface SkattemeldingDifference {
  type: string;
  name: string;
  amount: string;
  /** Added to the result (tillegg), or taken from it. */
  adds: boolean;
}

export type SkattemeldingStatus =
  | 'validert'
  | 'avvist'
  | 'sendt'
  | 'hos_revisor'
  | 'mottatt'
  | 'feilet';

export interface SkattemeldingAvvik {
  stage: string;
  type: string;
  path: string | null;
  received: string | null;
  computed: string | null;
}

export interface SkattemeldingVeiledning {
  type: string;
  text: string | null;
  strategy: string | null;
  path: string | null;
  /** `faktiskFeil`: what stops it. The rest is advice. */
  is_error: boolean;
}

export interface SkattemeldingFiling {
  id: number;
  company_id: number;
  year: number;
  status: SkattemeldingStatus;
  validation_status: string;
  validated_at: string;
  aarsresultat: string;
  skattemessig_resultat: string;
  /** What Skatteetaten computed, in whole kroner. */
  beregnet_skatt: string | null;
  has_auditor: boolean;
  ytelser_til_aksjonaerer: boolean;
  /** Filed early, because the company was wound up in the year. */
  forhaandsfastsetting?: boolean;
  /** What the person stated the shareholders were paid, in whole kroner. */
  likvidasjonsvederlag?: number | null;
  findings: SkattemeldingFinding[];
  avvik: SkattemeldingAvvik[];
  veiledninger: SkattemeldingVeiledning[];
  reasons: string[];
  /** On the year: whether the books still build what was checked. */
  matches_books: boolean | null;
  altinn_instance_id: string | null;
  /** Skatteetaten's own view of the instance, where a revisor confirms. */
  viewer_url: string | null;
  submitted_at: string | null;
  submitted_by_name: string | null;
  outcome: string | null;
  outcome_text: string | null;
  feedback_received_at: string | null;
  error: string | null;
  documents: { data_type: string; filename: string; content_type: string; byte_size: number }[];
}

export interface SkattemeldingYear {
  company_id: number;
  year: number;
  supported_years: number[];
  organisation_form: string | null;
  year_closed: boolean;
  /** The year's last day: 31 December, or a wound-up company's last day. */
  last_day: string;
  is_over: boolean;
  /**
   * The wound-up company's last year, filed early as a forhåndsfastsetting,
   * with the payout to the shareholders the books suggest.
   */
  forhaandsfastsetting: boolean;
  likvidasjonsvederlag_forslag: string | null;
  /** The register has deleted the company and no longer lists its roles. */
  revisor_unknown: boolean;
  submission_available: boolean;
  regnskapsplikt: string;
  rules: string | null;
  /** null: Myntos has no answer from Enhetsregisteret for the company. */
  has_auditor: boolean | null;
  auditor_name: string | null;
  posts: SkattemeldingPost[];
  saldi: SkattemeldingSaldo[];
  permanent_differences: SkattemeldingDifference[];
  equity_opening: string;
  equity_changes: SkattemeldingDifference[];
  equity_closing: string;
  aarsresultat: string;
  tillegg: string;
  fradrag: string;
  skattemessig_resultat: string;
  /** Whole kroner. */
  formue: { with_discount: number; without_discount: number; debt: number };
  findings: SkattemeldingFinding[];
  blocked: boolean;
  /** Newest first. */
  filings: SkattemeldingFiling[];
}

/** The year as the books have it now, or last year when `year` is left out. */
export const getSkattemeldingYear = (companyId: number, year?: number): Promise<SkattemeldingYear> =>
  request<SkattemeldingYear>(
    `/api/v1/companies/${companyId}/skattemelding${year === undefined ? '' : `?aar=${year}`}`,
  );

/** What only the person can say about a wound-up or deleted company. */
export interface FinalYearAnswers {
  /** The last year only (`forhaandsfastsetting`): paid to the shareholders in all, whole kroner. */
  likvidasjonsvederlag?: number;
  /** Only for a company the register has deleted (`revisor_unknown`). */
  revisor?: boolean;
}

/**
 * Have Skatteetaten check the year. Files nothing.
 *
 * Needs the ID-porten login; rejects with 409 when there is none, and with
 * `skattemelding_not_ready` while the year's review has a feil. For a
 * wound-up company's last year it also needs `finalYear`, and may reject
 * with `skattemelding_not_available` while Skatteetaten prepares the melding.
 */
export const checkSkattemelding = (
  companyId: number,
  year: number,
  ytelserTilAksjonaerer: boolean,
  finalYear?: FinalYearAnswers,
): Promise<SkattemeldingFiling> =>
  json<SkattemeldingFiling>(`/api/v1/companies/${companyId}/skattemelding/kontroll`, 'POST', {
    aar: year,
    ytelser_til_aksjonaerer: ytelserTilAksjonaerer,
    ...(finalYear ?? {}),
  });

/**
 * Irreversible: files the checked skattemelding through Altinn.
 *
 * `skattemessigResultat` is the figure the person confirmed. The backend
 * refuses unless it is exactly the checked one, and refuses
 * (`skattemelding_changed`) if the books have moved since the check.
 */
export const submitSkattemelding = (
  companyId: number,
  filingId: number,
  skattemessigResultat: string,
): Promise<SkattemeldingFiling> =>
  json<SkattemeldingFiling>(
    `/api/v1/companies/${companyId}/skattemelding/innsendinger/${filingId}/send`,
    'POST',
    { skattemessig_resultat: skattemessigResultat, bekreftet: true },
  );

/** Fetch Skatteetaten's answer once it has processed the melding. */
export const fetchSkattemeldingAnswer = (
  companyId: number,
  filingId: number,
): Promise<SkattemeldingFiling> =>
  request<SkattemeldingFiling>(
    `/api/v1/companies/${companyId}/skattemelding/innsendinger/${filingId}/tilbakemelding`,
    { method: 'POST' },
  );

/** `naeringsspesifikasjon`, `skattemelding`, `forespoersel`, `validering`, or a stored document's type. */
export const skattemeldingDocumentUrl = (companyId: number, filingId: number, type: string): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/skattemelding/innsendinger/${filingId}/dokument?type=${encodeURIComponent(type)}`;

// --- SAF-T ----------------------------------------------------------------------

export type SaftVersion = '1.30' | '1.40';

/**
 * One thing the cross-reference check found. `feil` stops the download: the file
 * would pass Altinn's schema check and fail at the auditor. `merknad` is worth
 * reading and may well be right.
 */
export interface SaftFinding {
  severity: 'feil' | 'merknad';
  code: string;
  message: string;
}

/** What the backend's check says about one financial year, before any file exists. */
export interface SaftCheck {
  company_id: number;
  year: number;
  /** The version the file will be written in. */
  version: SaftVersion;
  /** Every version the year may be written in; one from 2027. */
  versions: SaftVersion[];
  exportable: boolean;
  number_of_entries: number;
  total_debit: string;
  total_credit: string;
  findings: SaftFinding[];
}

const saftQuery = (year: number, version: SaftVersion | null): string =>
  `aar=${year}` + (version === null ? '' : `&versjon=${encodeURIComponent(version)}`);

export const checkSaft = (
  companyId: number,
  year: number,
  version: SaftVersion | null = null,
): Promise<SaftCheck> =>
  request<SaftCheck>(`/api/v1/companies/${companyId}/saft/kontroll?${saftQuery(year, version)}`);

/**
 * The file itself, as a link: the browser downloads it with the session cookie.
 * The backend refuses (422, `saft_not_exportable`) whenever the check above has a
 * `feil`, so offer the link only when `exportable` is true.
 */
export const saftFileUrl = (companyId: number, year: number, version: SaftVersion | null = null): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/saft/fil?${saftQuery(year, version)}`;

// --- Bank -----------------------------------------------------------------------
//
// camt.053 import and reconciliation. Matching only ever links a bank line to a
// voucher; posting from a line goes through the register's own path. Nothing
// here can change a posted voucher.

export interface BankStatement {
  id: number;
  company_id: number;
  bank_account_id: number;
  account: string;
  account_label: string | null;
  statement_ref: string;
  from_date: string;
  to_date: string;
  /** Signed: a balance is a position, and an overdrawn account is negative. */
  opening_balance: string;
  closing_balance: string;
  lines_new: number;
  lines_known: number;
  filename: string;
  camt_version: string;
  sha256: string;
  imported_at: string;
}

export interface BankImport {
  statements: BankStatement[];
  /** The same file had been imported before; nothing new was stored. */
  already_imported: boolean;
  /** Entries the bank had not booked yet, left out. */
  skipped_unbooked: number;
}

export interface BankSuggestion {
  voucher_id: number;
  voucher_no: string;
  occurred_on: string;
  description: string;
  party_name: string | null;
  type_label: string | null;
  score: number;
  confidence: 'sterk' | 'mulig';
  reasons: string[];
}

export interface BankLine {
  id: number;
  company_id: number;
  statement_id: number;
  bank_account_id: number;
  booking_date: string;
  value_date: string | null;
  amount: string;
  direction: Direction;
  counterparty_name: string | null;
  remittance: string | null;
  kid: string | null;
  bank_reference: string | null;
  additional_info: string | null;
  voucher_id: number | null;
  voucher_no: string | null;
  match_kind: 'bekreftet' | 'opprettet' | null;
  matched_at: string | null;
  /** The matched voucher has been reversed since; the line is no longer explained. */
  voucher_reversed: boolean;
  suggestions: BankSuggestion[];
  invoice: { document_id: number; document_number: string | null; buyer_name: string } | null;
}

export type BankLineFilter = 'alle' | 'uavstemt' | 'avstemt';

/** The account in the file is not one of the company's; the page links to where accounts are added. */
export const UNKNOWN_STATEMENT_ACCOUNT = 'unknown_statement_account';

export const importBankStatement = (companyId: number, file: File): Promise<BankImport> => {
  const form = new FormData();
  form.append('file', file);
  // No Content-Type header: the browser must set the multipart boundary itself.
  return request<BankImport>(`/api/v1/companies/${companyId}/bank/kontoutskrifter`, {
    method: 'POST',
    body: form,
  });
};

export const listBankStatements = (companyId: number): Promise<BankStatement[]> =>
  request<BankStatement[]>(`/api/v1/companies/${companyId}/bank/kontoutskrifter`);

export const bankStatementFileUrl = (companyId: number, statementId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/bank/kontoutskrifter/${statementId}/fil`;

export const listBankLines = (companyId: number, filter: BankLineFilter = 'alle'): Promise<BankLine[]> =>
  request<BankLine[]>(`/api/v1/companies/${companyId}/bank/linjer?status=${filter}`);

export const confirmBankMatch = (companyId: number, lineId: number, voucherId: number): Promise<BankLine> =>
  json<BankLine>(`/api/v1/companies/${companyId}/bank/linjer/${lineId}/avstemming`, 'POST', {
    voucher_id: voucherId,
  });

export const removeBankMatch = (companyId: number, lineId: number): Promise<BankLine> =>
  request<BankLine>(`/api/v1/companies/${companyId}/bank/linjer/${lineId}/avstemming`, {
    method: 'DELETE',
  });

/** Post a voucher for the line. Amount, date and direction are the bank's and are not sent. */
export const createFromBankLine = (
  companyId: number,
  lineId: number,
  payload: { type_code: string; description: string; tax_code?: string | null; party_id?: number | null },
): Promise<BankLine> =>
  json<BankLine>(`/api/v1/companies/${companyId}/bank/linjer/${lineId}/bilag`, 'POST', payload);

// --- Inngående balanse og årsavslutning --------------------------------------------
//
// Both are review-then-post. The review comes from the backend (`.../kontroll`,
// the closing plan) and the page shows it as it is: the sums and the findings
// are the server's, never recomputed here, for the reason lib/format.ts gives.

export interface YearFinding {
  severity: 'feil' | 'merknad';
  code: string;
  message: string;
}

/** One opening line as typed or as read from a saldobalanse. Amounts are strings. */
export interface OpeningLineInput {
  account_number: string;
  debit: string;
  credit: string;
  party_id: number | null;
  description: string | null;
}

export interface OpeningLine extends OpeningLineInput {
  account_description: string | null;
  party_name: string | null;
}

export interface OpeningReview {
  company_id: number;
  year: number | null;
  lines: OpeningLine[];
  total_debit: string;
  total_credit: string;
  difference: string;
  postable: boolean;
  findings: YearFinding[];
}

export interface OpeningVoucher {
  id: number;
  voucher_no: string;
  transaction_date: string;
  posted_at: string | null;
  is_reversal: boolean;
  reversed: boolean;
  corrects_id: number | null;
  total: string;
  lines: OpeningLine[];
}

export interface OpeningState {
  company_id: number;
  year: number | null;
  allowed: boolean;
  refusal: string | null;
  standing: OpeningVoucher | null;
  history: OpeningVoucher[];
}

export interface PostedYearVoucher {
  company_id: number;
  id: number;
  voucher_no: string;
  transaction_date: string;
  description: string;
  total: string;
}

const openingPath = (companyId: number): string => `/api/v1/companies/${companyId}/inngaende-balanse`;

export const getOpeningState = (companyId: number): Promise<OpeningState> =>
  request<OpeningState>(openingPath(companyId));

export const reviewOpening = (companyId: number, lines: OpeningLineInput[]): Promise<OpeningReview> =>
  json<OpeningReview>(`${openingPath(companyId)}/kontroll`, 'POST', { lines });

/** Reads a saldobalanse CSV into lines and reviews them. Nothing is posted or kept. */
export const readSaldobalanse = (companyId: number, file: File): Promise<OpeningReview> => {
  const form = new FormData();
  form.append('file', file);
  return request<OpeningReview>(`${openingPath(companyId)}/saldobalanse`, { method: 'POST', body: form });
};

/** An account a SAF-T file has a balance on that the kontoplan does not have yet. */
export interface SaftAccountToAdd {
  number: string;
  description: string;
  account_type: AccountType;
  grouping_category: string;
  grouping_code: string;
  /** The file's own account ids that land on this number. */
  source_accounts: string[];
}

/** A customer or supplier with open items in a SAF-T file that Myntos does not have. */
export interface SaftPartyToAdd {
  party_type: PartyType;
  name: string;
  orgnr: string | null;
  party_id: string | null;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  source_id: string;
}

export interface SaftOpening extends OpeningReview {
  source: {
    software: string | null;
    audit_file_version: string;
    orgnr: string;
    company_name: string;
    start_date: string;
    end_date: string;
    /** Which of the file's balances were read: its opening ones, or the year before's closing. */
    balances: 'inngaende' | 'utgaende';
  };
  accounts_to_add: SaftAccountToAdd[];
  parties_to_add: SaftPartyToAdd[];
}

/** Reads the previous program's SAF-T file (XML or ZIP) into lines. Nothing is posted or kept. */
export const readSaft = (companyId: number, file: File): Promise<SaftOpening> => {
  const form = new FormData();
  form.append('file', file);
  return request<SaftOpening>(`${openingPath(companyId)}/saft`, { method: 'POST', body: form });
};

// --- The old program's vouchers, and the two programs compared ------------------------
//
// The same SAF-T file again, read for its vouchers. It is uploaded with every
// call and kept by nobody, so the page holds on to the File between them.

export interface SaftFileSource {
  software: string | null;
  audit_file_version: string;
  orgnr: string;
  company_name: string;
  start_date: string;
  end_date: string;
}

export interface SaftVoucherImport {
  company_id: number;
  source: SaftFileSource;
  year: number;
  /** The last day brought over: `til`, or the file's last voucher. */
  through: string;
  vouchers: number;
  lines: number;
  first_date: string | null;
  last_date: string | null;
  already_imported: number;
  /** Vouchers dated after `through`, left for later. */
  later: number;
  months: { month: number; vouchers: number; total: string }[];
  accounts_to_add: SaftAccountToAdd[];
  parties_to_add: SaftPartyToAdd[];
  findings: YearFinding[];
  postable: boolean;
}

export interface ImportedVouchers {
  company_id: number;
  posted: number;
  journal: string;
  first_voucher_no: string;
  last_voucher_no: string;
  first_date: string;
  last_date: string;
}

export interface SaftComparison {
  company_id: number;
  source: SaftFileSource;
  start_date: string;
  through: string;
  matches: boolean;
  accounts_compared: number;
  parties_compared: number;
  terms_compared: number;
  accounts: {
    account_number: string;
    description: string;
    in_file: string;
    in_myntos: string;
    difference: string;
    months: number[];
  }[];
  parties: {
    party_type: PartyType;
    name: string;
    source_id: string | null;
    party_id: number | null;
    in_file: string;
    in_myntos: string;
    difference: string;
  }[];
  vat: {
    termin: string;
    code: string;
    spesifikasjon: string | null;
    base_in_file: string;
    base_in_myntos: string;
    vat_in_file: string;
    vat_in_myntos: string;
  }[];
  findings: YearFinding[];
}

const saftPath = (companyId: number): string => `/api/v1/companies/${companyId}/saft`;

function saftForm(file: File, til: string | null): FormData {
  const form = new FormData();
  form.append('file', file);
  if (til !== null && til !== '') form.append('til', til);
  return form;
}

/** What bringing the year's vouchers over from `file` would post. Nothing is posted or kept. */
export const reviewSaftVouchers = (
  companyId: number,
  file: File,
  til: string | null,
): Promise<SaftVoucherImport> =>
  request<SaftVoucherImport>(`${saftPath(companyId)}/bilag/kontroll`, {
    method: 'POST',
    body: saftForm(file, til),
  });

/** Posts the year's vouchers from `file`, all of them or none. */
export const postSaftVouchers = (
  companyId: number,
  file: File,
  til: string | null,
): Promise<ImportedVouchers> =>
  request<ImportedVouchers>(`${saftPath(companyId)}/bilag`, {
    method: 'POST',
    body: saftForm(file, til),
  });

/** The old program's books against Myntos's, from a fresh SAF-T file. Nothing is kept. */
export const compareWithSaft = (
  companyId: number,
  file: File,
  til: string | null,
): Promise<SaftComparison> =>
  request<SaftComparison>(`${saftPath(companyId)}/sammenligning`, {
    method: 'POST',
    body: saftForm(file, til),
  });

// --- The previous program's files, read on the server ---------------------------------
//
// Uploaded once, then read, compared and posted by the server, which the page
// polls. Nothing needs the page to stay open after the upload: see
// backend/src/myntos/saft/jobs.py for why that replaced the calls above.

export type SaftImportStatus = 'reading' | 'ready' | 'posting' | 'posted' | 'failed';

export interface SaftImportFinding extends YearFinding {
  /** The year it is about, or null for the files as a whole. */
  year: number | null;
}

export interface SaftImportYear {
  year: number;
  file_name: string;
  software: string | null;
  start_date: string;
  end_date: string;
  /** Whether the file runs to 31 December; only the last year may not. */
  complete: boolean;
  through: string;
  /** Whether the year is new in Myntos. */
  opened: boolean;
  vouchers: number;
  lines: number;
  first_date: string | null;
  last_date: string | null;
  already_imported: number;
  later: number;
  months: { month: number; vouchers: number; total: string }[];
}

export interface SaftDimensionToAdd {
  code: string;
  dimension_description: string;
  dimension_id: number | null;
  value: string;
  description: string;
}

export interface SaftMoveReview {
  kind: 'import';
  postable: boolean;
  vouchers: number;
  first_year_before: number | null;
  first_year_after: number;
  years_opened: number[];
  years: SaftImportYear[];
  opening: {
    year: number;
    action: 'post' | 'replace' | 'keep' | 'none';
    replaces: string | null;
    replaces_year: number | null;
    lines: number;
    total: string;
  } | null;
  accounts_to_add: SaftAccountToAdd[];
  parties_to_add: SaftPartyToAdd[];
  dimensions_to_add: SaftDimensionToAdd[];
  findings: SaftImportFinding[];
}

export type SaftComparisonReview = Omit<SaftComparison, 'company_id' | 'findings'> & {
  kind: 'sammenligning';
  findings: SaftImportFinding[];
};

export interface SaftImport {
  company_id: number;
  id: number;
  purpose: 'import' | 'sammenligning';
  status: SaftImportStatus;
  til: string | null;
  created_at: string;
  updated_at: string;
  posted_at: string | null;
  /** What the server is doing now, in Norwegian. */
  step: string | null;
  done: number;
  total: number;
  running: boolean;
  files_kept: boolean;
  files: { filename: string; size: number; year: number | null }[];
  review: SaftMoveReview | SaftComparisonReview | null;
  result: SaftMoveReview | null;
  error: string | null;
  error_code: string | null;
  may_post: boolean;
}

const importPath = (companyId: number): string => `${saftPath(companyId)}/innlesing`;

/**
 * Uploads SAF-T files to be read on the server, reporting how much has gone up.
 */
export function uploadSaftFiles(
  companyId: number,
  files: File[],
  options: { til?: string | null; compare?: boolean },
  onProgress: (sent: number, total: number) => void,
): Promise<SaftImport> {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  if (options.til) form.append('til', options.til);
  if (options.compare) form.append('sammenlign', 'true');
  return uploadWithProgress<SaftImport>(companyId, importPath(companyId), form, onProgress);
}

/**
 * Posts `form` to `path`, reporting how much has gone up.
 *
 * XMLHttpRequest rather than `request`, because fetch cannot say how far an
 * upload has come, and a year of vouchers is a long upload on a farm's line:
 * a bar that does not move reads as a page that froze. The company wall is the
 * same as `request`'s, checked before sending and on the answer.
 */
function uploadWithProgress<T>(
  companyId: number,
  path: string,
  form: FormData,
  onProgress: (sent: number, total: number) => void,
): Promise<T> {
  const active = activeCompanyId();
  if (active !== null && active !== companyId) {
    return Promise.reject(
      new WrongCompanyError(
        'Vi stoppet en forespørsel som gjaldt et annet selskap enn det du ser på. Last siden på nytt.',
        active,
        companyId,
      ),
    );
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE_URL}${path}`);
    xhr.withCredentials = true;
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    });
    xhr.addEventListener('error', () =>
      reject(new ApiError(0, 'Fikk ikke kontakt med serveren.', 'network')),
    );
    xhr.addEventListener('load', () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const code =
          typeof body === 'object' && body !== null && typeof (body as { code?: unknown }).code === 'string'
            ? (body as { code: string }).code
            : null;
        reject(new ApiError(xhr.status, messageFrom(xhr.status, body), code));
        return;
      }
      try {
        assertCompanyScope(companyId, body);
      } catch (error) {
        reject(error);
        return;
      }
      resolve(body as T);
    });
    xhr.send(form);
  });
}

export const listSaftImports = (companyId: number): Promise<SaftImport[]> =>
  request<SaftImport[]>(importPath(companyId));

export const getSaftImport = (companyId: number, importId: number): Promise<SaftImport> =>
  request<SaftImport>(`${importPath(companyId)}/${importId}`);

/** Starts posting what the review showed. The server does it; poll the import. */
export const postSaftImport = (companyId: number, importId: number): Promise<SaftImport> =>
  request<SaftImport>(`${importPath(companyId)}/${importId}/foring`, { method: 'POST' });

export const discardSaftImport = (companyId: number, importId: number): Promise<void> =>
  request<void>(`${importPath(companyId)}/${importId}`, { method: 'DELETE' });

// --- The documents behind the vouchers, from the old program's printout ---------------
//
// The same upload, read, review and yes as the SAF-T files, on the same kind
// of record: see backend/src/myntos/saft/documents.py for how a document finds
// its voucher.

export type DocumentAction =
  | 'attach'
  | 'already_attached'
  | 'no_attachments'
  | 'not_in_books'
  | 'too_large'
  | 'repeated';

export interface PrintedVoucher {
  /** Tripletex's number, «240-2026». */
  number: string;
  file_name: string;
  first_page: number;
  pages: number;
  /** The pages after Tripletex's own: the receipts and invoices. */
  attachment_pages: number;
  printed_on: string | null;
  action: DocumentAction;
  sha256: string | null;
  byte_size: number | null;
  printed_debit: string | null;
  booked_debit: string | null;
  /** The vouchers in Myntos it goes on: more than one when the import split it by date. */
  targets: { voucher_id: number; voucher_no: string; already: boolean }[];
}

export interface DocumentReview {
  kind: 'vedlegg';
  postable: boolean;
  software: string;
  files: { file_name: string; pages: number; vouchers: number; loose_pages: number }[];
  vouchers_in_files: number;
  to_attach: number;
  /** How many files go into the archive: one per voucher in Myntos. */
  documents: number;
  already_attached: number;
  no_attachments: number;
  not_in_books: number;
  too_large: number;
  repeated: number;
  vouchers: PrintedVoucher[];
  findings: SaftImportFinding[];
}

export interface DocumentImport {
  company_id: number;
  id: number;
  status: SaftImportStatus;
  created_at: string;
  updated_at: string;
  posted_at: string | null;
  step: string | null;
  done: number;
  total: number;
  running: boolean;
  files_kept: boolean;
  files: { filename: string; size: number }[];
  review: DocumentReview | null;
  result: DocumentReview | null;
  error: string | null;
  error_code: string | null;
  may_post: boolean;
}

const documentsPath = (companyId: number): string => `${saftPath(companyId)}/vedlegg`;

/** Uploads the old program's printouts to be read on the server, reporting how much has gone up. */
export function uploadPrintouts(
  companyId: number,
  files: File[],
  onProgress: (sent: number, total: number) => void,
): Promise<DocumentImport> {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  return uploadWithProgress<DocumentImport>(companyId, documentsPath(companyId), form, onProgress);
}

export const listDocumentImports = (companyId: number): Promise<DocumentImport[]> =>
  request<DocumentImport[]>(documentsPath(companyId));

export const getDocumentImport = (companyId: number, importId: number): Promise<DocumentImport> =>
  request<DocumentImport>(`${documentsPath(companyId)}/${importId}`);

/** Starts putting the documents in the bilag archive. The server does it; poll the upload. */
export const archiveDocumentImport = (
  companyId: number,
  importId: number,
): Promise<DocumentImport> =>
  request<DocumentImport>(`${documentsPath(companyId)}/${importId}/arkivering`, { method: 'POST' });

export const discardDocumentImport = (companyId: number, importId: number): Promise<void> =>
  request<void>(`${documentsPath(companyId)}/${importId}`, { method: 'DELETE' });

// --- The bilagsjournal ----------------------------------------------------------------

export interface VoucherLine {
  account_number: string;
  account_description: string;
  description: string | null;
  debit: string;
  credit: string;
  party_name: string | null;
  tax_code: string | null;
  dimensions: string[];
}

export interface Voucher {
  id: number;
  journal: string;
  journal_description: string;
  voucher_no: string;
  transaction_date: string;
  description: string;
  voucher_type: string | null;
  source_document: string | null;
  total: string;
  reverses_id: number | null;
  reversed_by_id: number | null;
  created_by_name: string | null;
  lines: VoucherLine[];
  /** With an account asked about: the voucher's movement on it, debit positive. */
  movement: string | null;
  balance: string | null;
  /** The files the bilag archive holds for it, each downloaded with `documentUrl`. */
  documents: VoucherDocument[];
}

export interface VoucherDocument {
  id: number;
  filename: string;
  content_type: string;
  byte_size: number;
}

export interface VoucherPage {
  company_id: number;
  year: number;
  month: number | null;
  total_count: number;
  offset: number;
  vouchers: Voucher[];
  journals: { code: string; description: string; vouchers: number }[];
  account_number: string | null;
  account_description: string | null;
  opening_balance: string | null;
  closing_balance: string | null;
}

export interface VoucherFilter {
  year: number;
  month?: number | null;
  journal?: string | null;
  account?: string | null;
  search?: string | null;
  /** Only vouchers with a document in the bilag archive. */
  withDocuments?: boolean;
  /** The newest first; the API's default is the oldest first. */
  newestFirst?: boolean;
  offset?: number;
}

export function listVouchers(companyId: number, filter: VoucherFilter): Promise<VoucherPage> {
  const query = new URLSearchParams({ aar: String(filter.year) });
  if (filter.month) query.set('maaned', String(filter.month));
  if (filter.journal) query.set('journal', filter.journal);
  if (filter.account) query.set('konto', filter.account);
  if (filter.search) query.set('sok', filter.search);
  if (filter.withDocuments) query.set('med_dokument', 'true');
  if (filter.newestFirst) query.set('rekkefolge', 'nyeste');
  if (filter.offset) query.set('fra', String(filter.offset));
  return request<VoucherPage>(`/api/v1/companies/${companyId}/bilag?${query.toString()}`);
}

/** Posts the opening balance; `correct` reverses the standing one and replaces it. */
export const postOpening = (
  companyId: number,
  lines: OpeningLineInput[],
  correct: boolean,
): Promise<PostedYearVoucher> =>
  json<PostedYearVoucher>(openingPath(companyId), 'POST', { lines, korriger: correct });

export const reverseOpening = (companyId: number): Promise<PostedYearVoucher> =>
  request<PostedYearVoucher>(`${openingPath(companyId)}/reversering`, { method: 'POST' });

export interface YearEvent {
  /** `opplost`: the books ended early at the dissolution; `opplost_angret`: undone. */
  action: 'avsluttet' | 'gjenapnet' | 'opplost' | 'opplost_angret';
  occurred_at: string;
  user_id: number | null;
  reason: string | null;
  result: string | null;
  tax: string | null;
}

export interface FinancialYearStatus {
  company_id: number;
  year: number;
  start_date: string;
  end_date: string;
  closed: boolean;
  closed_at: string | null;
  events: YearEvent[];
}

export const listFinancialYears = (companyId: number): Promise<FinancialYearStatus[]> =>
  request<FinancialYearStatus[]>(`/api/v1/companies/${companyId}/regnskapsaar`);

export interface PlannedLine {
  account_number: string;
  description: string;
  debit: string;
  credit: string;
}

export interface ClosingPlan {
  company_id: number;
  year: number;
  organisation_form: string | null;
  closed: boolean;
  closed_at: string | null;
  revenue: string;
  costs: string;
  result_before_tax: string;
  tax: string;
  result: string;
  tax_lines: PlannedLine[];
  disposition_lines: PlannedLine[];
  closable: boolean;
  findings: YearFinding[];
}

const closingPath = (companyId: number): string => `/api/v1/companies/${companyId}/arsavslutning`;

export const getClosingPlan = (companyId: number, year: number, tax: string): Promise<ClosingPlan> =>
  request<ClosingPlan>(
    `${closingPath(companyId)}?${new URLSearchParams({ aar: String(year), skattekostnad: tax || '0' })}`,
  );

export const closeYear = (
  companyId: number,
  year: number,
  tax: string,
): Promise<ClosingPlan & { vouchers: PostedYearVoucher[] }> =>
  json(closingPath(companyId), 'POST', { aar: year, skattekostnad: tax || '0', bekreftet: true });

export const reopenYear = (
  companyId: number,
  year: number,
  reason: string,
): Promise<{ company_id: number; year: number; reversals: PostedYearVoucher[] }> =>
  json(`${closingPath(companyId)}/gjenapning`, 'POST', { aar: year, begrunnelse: reason, bekreftet: true });

/** Where a company's books stand at the dissolution. */
export interface WindingUp {
  company_id: number;
  /** The last day of the books, or null while the company is in business. */
  last_day: string | null;
  year: number | null;
  year_closed: boolean;
}

/**
 * End the books on a dissolved company's last day (an ISO date). The year then
 * ends there, nothing can be posted after it, and its skattemelding is a
 * forhåndsfastsetting. Refused with `winding_up_conflict` when anything is
 * dated after the day, and `invalid_winding_up_date` for a day in the future.
 */
export const windUp = (companyId: number, lastDay: string): Promise<WindingUp> =>
  json<WindingUp>(`${closingPath(companyId)}/opplosning`, 'POST', {
    siste_dag: lastDay,
    bekreftet: true,
  });

/** Let the last year run to 31 December again, while it is open. */
export const undoWindUp = (companyId: number): Promise<WindingUp> =>
  request<WindingUp>(`${closingPath(companyId)}/opplosning/angre`, { method: 'POST' });

// --- Manuelt bilag ----------------------------------------------------------------
//
// Accounts with debit and credit and a written reason, no bank line: the
// year-end adjustments. Review-then-post like the opening balance, and the
// review is the backend's. Corrections and reversals are their own routes,
// because the register's reversal refuses a manual voucher.

/** One line as typed. With a `tax_code` the amount includes VAT. */
export interface ManualLineInput {
  account_number: string;
  debit: string;
  credit: string;
  description: string | null;
  party_id: number | null;
  tax_code: string | null;
  /** Kept through a correction so it does not drop what an agent or the register set. */
  dimension_value_ids?: number[];
}

export interface ManualVoucherInput {
  occurred_on: string;
  description: string;
  /** Why it is posted (bokføringsforskriften § 5-12). Filed beside the voucher for good. */
  explanation: string;
  lines: ManualLineInput[];
}

/** A line as it is (or would be) posted. `generated` marks the VAT lines Myntos adds. */
export interface ManualLine {
  from_line: number;
  account_number: string;
  account_description: string | null;
  description: string;
  debit: string;
  credit: string;
  party_id: number | null;
  party_name: string | null;
  tax_code: string | null;
  vat_amount: string | null;
  generated: boolean;
  dimension_value_ids: number[];
}

export interface ManualReview {
  company_id: number;
  year: number;
  occurred_on: string;
  description: string;
  explanation: string;
  corrects_id: number | null;
  lines: ManualLine[];
  total_debit: string;
  total_credit: string;
  difference: string;
  postable: boolean;
  findings: YearFinding[];
}

export interface ManualVoucher {
  company_id: number;
  id: number;
  voucher_no: string;
  journal: string;
  occurred_on: string;
  posted_at: string | null;
  description: string;
  explanation: string | null;
  total: string;
  lines: ManualLine[];
  /** The voucher as it was typed: what a correction starts from. */
  input_lines: (ManualLineInput & { dimension_value_ids: number[] })[];
  is_reversal: boolean;
  reverses_id: number | null;
  reversed_by_id: number | null;
  corrects_id: number | null;
  corrected_by_id: number | null;
  created_by_id: number | null;
  created_by_name: string | null;
  created_via: string | null;
  attachments: AttachmentInfo[];
}

export interface ManualVoucherList {
  company_id: number;
  year: number;
  closed: boolean;
  tax_codes: TaxCode[];
  entries: ManualVoucher[];
}

const manualPath = (companyId: number): string => `/api/v1/companies/${companyId}/manuelt-bilag`;

export const listManualVouchers = (companyId: number, year: number): Promise<ManualVoucherList> =>
  request<ManualVoucherList>(`${manualPath(companyId)}?aar=${year}`);

export const getManualVoucher = (companyId: number, voucherId: number): Promise<ManualVoucher> =>
  request<ManualVoucher>(`${manualPath(companyId)}/${voucherId}`);

/** The review. Stores nothing; `correctsId` reviews it as the replacement of that voucher. */
export const reviewManualVoucher = (
  companyId: number,
  voucher: ManualVoucherInput,
  correctsId: number | null = null,
): Promise<ManualReview> =>
  json<ManualReview>(`${manualPath(companyId)}/kontroll`, 'POST', { ...voucher, corrects_id: correctsId });

export const postManualVoucher = (companyId: number, voucher: ManualVoucherInput): Promise<ManualVoucher> =>
  json<ManualVoucher>(manualPath(companyId), 'POST', voucher);

/** Reverses `voucherId` and posts `voucher` in its place, linked. Answers with the replacement. */
export const correctManualVoucher = (
  companyId: number,
  voucherId: number,
  voucher: ManualVoucherInput,
): Promise<ManualVoucher> => json<ManualVoucher>(`${manualPath(companyId)}/${voucherId}`, 'PUT', voucher);

export const reverseManualVoucher = (
  companyId: number,
  voucherId: number,
  explanation: string,
): Promise<ManualVoucher> =>
  json<ManualVoucher>(`${manualPath(companyId)}/${voucherId}/reversering`, 'POST', { explanation });

// --- Kontoplan --------------------------------------------------------------------

export interface LedgerAccount {
  id: number;
  number: string;
  description: string;
  account_type: AccountType;
  is_active: boolean;
  /** Used by Myntos itself: cannot be deactivated or deleted. */
  is_locked: boolean;
  /** Anything ever posted to it; such an account can only be deactivated. */
  has_postings: boolean;
  created_date: string | null;
  /** Null only for an account added after the year was closed. */
  grouping_category: string | null;
  grouping_code: string | null;
  /** Set when a person chose the post; null when the seed or the default did. */
  grouping_chosen_at: string | null;
  /**
   * The company's money sits here, so the overview counts it as the bank:
   * always 1920 and 1900, and any other account marked as a bank account.
   */
  is_bank_account: boolean;
}

export interface GroupingOption {
  category: string;
  code: string;
  description: string;
}

export interface Kontoplan {
  year: number;
  /** A closed year's groupings are not changed. */
  closed: boolean;
  /** Whose published list the options come from; differs from `year` before one is out. */
  list_year: number;
  accounts: LedgerAccount[];
  groupings: GroupingOption[];
}

export const getKontoplan = (companyId: number, year: number): Promise<Kontoplan> =>
  request<Kontoplan>(`/api/v1/companies/${companyId}/kontoplan?aar=${year}`);

export const suggestAccount = (
  companyId: number,
  number: string,
  year: number,
): Promise<{ account_type: AccountType; grouping_category: string; grouping_code: string }> =>
  request(
    `/api/v1/companies/${companyId}/kontoplan/forslag?nummer=${encodeURIComponent(number)}&aar=${year}`,
  );

export const createAccount = (
  companyId: number,
  year: number,
  payload: {
    number: string;
    description: string;
    account_type?: AccountType | null;
    grouping_category?: string | null;
    grouping_code?: string | null;
  },
): Promise<Kontoplan> =>
  json<Kontoplan>(`/api/v1/companies/${companyId}/kontoplan?aar=${year}`, 'POST', payload);

export const updateAccount = (
  companyId: number,
  accountId: number,
  year: number,
  payload: { description?: string; is_active?: boolean; is_bank_account?: boolean },
): Promise<Kontoplan> =>
  json<Kontoplan>(`/api/v1/companies/${companyId}/kontoplan/${accountId}?aar=${year}`, 'PATCH', payload);

export const setGrouping = (
  companyId: number,
  accountId: number,
  payload: { year: number; grouping_category: string; grouping_code: string },
): Promise<Kontoplan> =>
  json<Kontoplan>(`/api/v1/companies/${companyId}/kontoplan/${accountId}/gruppering`, 'PUT', payload);

export const deleteAccount = (companyId: number, accountId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/kontoplan/${accountId}`, { method: 'DELETE' });

// --- Landbruk: the farming add-on ---------------------------------------------------

/**
 * Whether the books have the farming add-on, and why.
 *
 * `source` is `naeringskode` when the company's næringskode decided (01 and 02
 * have it unless somebody said no) and `valgt` when a person did.
 */
export interface Landbruk {
  enabled: boolean;
  source: 'naeringskode' | 'valgt';
  industry_code: string | null;
  /** The add-on's accounts the kontoplan does not have yet. */
  missing_accounts: string[];
  /** Set by a switch: what it added, and what it left alone under the same number. */
  added_accounts: string[];
  kept_accounts: string[];
}

export const getLandbruk = (companyId: number): Promise<Landbruk> =>
  request<Landbruk>(`/api/v1/companies/${companyId}/landbruk`);

/** `true` on, `false` off, `null` to hand the decision back to the næringskode. */
export const setLandbruk = (companyId: number, enabled: boolean | null): Promise<Landbruk> =>
  json<Landbruk>(`/api/v1/companies/${companyId}/landbruk`, 'PUT', { enabled });

// --- Landbruk: the year-end count and private uttak ---------------------------------
//
// Review-then-post, as the opening balance: the values, the VAT and the voucher
// are the backend's (`…/kontroll`), never computed here. The year is a query or
// body field, so the company id stays the only id in a path.

export type CountKind = 'buskap' | 'egenprodusert' | 'innkjopt';
export type CountValuation = 'normalverdi' | 'snitt' | 'kostpris' | 'verdsatt';

/** Which year's takseringsregler the catalogue is from, and whether the year asked for is out. */
export interface FarmRates {
  year: number;
  published: boolean;
  source: string;
  url: string;
}

export interface CountCategory {
  code: string;
  kind: CountKind;
  label: string;
  unit: string;
  value: string | null;
  valuation: CountValuation;
  species: string | null;
  adult: boolean;
  nibio: string | null;
}

/** One row as typed. Quantities and values are decimal strings. */
export interface CountLineInput {
  side: 'inngaende' | 'utgaende';
  category: string;
  quantity: string;
  description?: string;
  kind?: CountKind | null;
  unit?: string | null;
  valuation?: CountValuation | null;
  unit_value?: string | null;
  account_number?: string | null;
}

export interface CountLine {
  side: 'inngaende' | 'utgaende';
  kind: CountKind;
  category: string;
  label: string;
  description: string;
  quantity: string;
  unit: string;
  valuation: string;
  unit_value: string;
  value: string;
  account_number: string;
  nibio: string | null;
}

export interface CountKindTotal {
  kind: CountKind;
  naeringsspesifikasjon: string;
  opening: string;
  closing: string;
}

export interface CountAccountChange {
  account_number: string;
  account_description: string | null;
  kind: CountKind;
  booked: string;
  counted: string;
  change: string;
  opening_booked: string;
  opening_counted: string;
}

export interface FarmVoucherLine {
  account_number: string;
  description: string;
  debit: string;
  credit: string;
}

export interface CountReview {
  company_id: number;
  year: number;
  counted_on: string | null;
  opening_source: string;
  opening: CountLine[];
  closing: CountLine[];
  accounts: CountAccountChange[];
  voucher_lines: FarmVoucherLine[];
  totals: CountKindTotal[];
  postable: boolean;
  findings: YearFinding[];
}

export interface FarmCount {
  company_id: number;
  id: number;
  year: number;
  counted_on: string;
  opening_source: string;
  withdrawn: boolean;
  corrects_id: number | null;
  voucher_id: number | null;
  voucher_no: string | null;
  created_at: string;
  opening: CountLine[];
  closing: CountLine[];
  totals: CountKindTotal[];
}

export interface CountState {
  company_id: number;
  year: number;
  counted_on: string | null;
  allowed: boolean;
  refusal: string | null;
  rates: FarmRates;
  categories: CountCategory[];
  /** `forrige_telling`: last year's closing rows, unchanged; `oppgitt`: typed. */
  opening_source: 'forrige_telling' | 'oppgitt';
  opening: CountLine[];
  booked: { account_number: string; account_description: string; kind: CountKind; balance: string }[];
  standing: FarmCount | null;
  history: FarmCount[];
}

const countPath = (companyId: number): string => `/api/v1/companies/${companyId}/landbruk/telling`;

export const getCountState = (companyId: number, year: number): Promise<CountState> =>
  request<CountState>(`${countPath(companyId)}?aar=${year}`);

export const reviewCount = (companyId: number, year: number, lines: CountLineInput[]): Promise<CountReview> =>
  json<CountReview>(`${countPath(companyId)}/kontroll`, 'POST', { aar: year, lines });

/** Posts the count; `correct` reverses the standing one and replaces it. */
export const postCount = (
  companyId: number,
  year: number,
  lines: CountLineInput[],
  correct: boolean,
): Promise<FarmCount> => json<FarmCount>(countPath(companyId), 'POST', { aar: year, lines, korriger: correct });

export const withdrawCount = (companyId: number, year: number): Promise<FarmCount> =>
  json<FarmCount>(`${countPath(companyId)}/reversering`, 'POST', { aar: year });

export interface UttakProduct {
  code: string;
  label: string;
  unit: string;
  /** The norm per unit, or null for omsetningsverdi (the person states it). */
  value: string | null;
  account: string;
  food: boolean;
  note: string;
}

export interface UttakLineInput {
  product: string;
  quantity: string;
  description?: string;
  unit_value?: string | null;
  unit?: string | null;
  account_number?: string | null;
}

export interface UttakLine {
  product: string;
  label: string;
  description: string;
  quantity: string;
  unit: string;
  unit_value: string;
  value: string;
  account_number: string;
  tax_code: string | null;
  vat_rate: string | null;
  vat: string;
  vat_reason: 'fritatt' | 'avgiftspliktig' | 'uregistrert';
}

export interface UttakReview {
  company_id: number;
  year: number;
  occurred_on: string | null;
  lines: UttakLine[];
  total_value: string;
  total_vat: string;
  postable: boolean;
  findings: YearFinding[];
}

export interface FarmUttak {
  company_id: number;
  id: number;
  year: number;
  occurred_on: string;
  withdrawn: boolean;
  corrects_id: number | null;
  voucher_id: number | null;
  voucher_no: string | null;
  created_at: string;
  lines: UttakLine[];
  total_value: string;
  total_vat: string;
}

export interface UttakState {
  company_id: number;
  year: number;
  occurred_on: string | null;
  allowed: boolean;
  refusal: string | null;
  rates: FarmRates;
  products: UttakProduct[];
  standing: FarmUttak | null;
  history: FarmUttak[];
}

const uttakPath = (companyId: number): string => `/api/v1/companies/${companyId}/landbruk/uttak`;

export const getUttakState = (companyId: number, year: number): Promise<UttakState> =>
  request<UttakState>(`${uttakPath(companyId)}?aar=${year}`);

export const reviewUttak = (
  companyId: number,
  year: number,
  lines: UttakLineInput[],
  date: string | null,
): Promise<UttakReview> =>
  json<UttakReview>(`${uttakPath(companyId)}/kontroll`, 'POST', { aar: year, dato: date, lines });

export const postUttak = (
  companyId: number,
  year: number,
  lines: UttakLineInput[],
  date: string | null,
  correct: boolean,
): Promise<FarmUttak> =>
  json<FarmUttak>(uttakPath(companyId), 'POST', { aar: year, dato: date, lines, korriger: correct });

export const withdrawUttak = (companyId: number, year: number): Promise<FarmUttak> =>
  json<FarmUttak>(`${uttakPath(companyId)}/reversering`, 'POST', { aar: year });

// --- Dimensjoner: avdelinger, prosjekter, driftsgrener ------------------------------

/** One value of a dimension: avdeling 20 «Jordbruk», driftsgren 6110. */
export interface DimensionValue {
  /** What an entry sends in `dimension_value_ids`. */
  id: number;
  /** The code, never changed: «6110», «20», «P-104». */
  value: string;
  description: string;
  is_active: boolean;
  /** Posted lines carrying it. Above zero it is switched off, never deleted. */
  lines: number;
}

/** A second way to sort the books beside the account (SAF-T's analysis type). */
export interface Dimension {
  id: number;
  code: string;
  description: string;
  is_active: boolean;
  /** `AVDELING`, `PROSJEKT`, `DRIFTSGR` or `PRODUKT` when Myntos knows it as one. */
  kind: string | null;
  /** What the report calls lines with no value: «Uten driftsgren». */
  without_label: string;
  values: DimensionValue[];
}

/** NIBIO's lists, offered only when the books have the farming add-on. */
export interface DimensionSuggestions {
  landbruk: boolean;
  source: string | null;
  sets: {
    kind: string;
    description: string;
    dimension_id: number | null;
    values: { code: string; name: string; group: string; added: boolean }[];
  }[];
}

export interface DimensionFigures {
  income: string;
  costs: string;
  result: string;
}

export interface DimensionResultRow {
  /** Null on «Uten …» and on the total. */
  value_id: number | null;
  value: string | null;
  description: string;
  is_active: boolean;
  total: DimensionFigures;
  /** Twelve, January first. */
  months: (DimensionFigures & { month: number })[];
}

export interface DimensionResult {
  year: number;
  dimension: Dimension;
  rows: DimensionResultRow[];
  unallocated: DimensionResultRow;
  total: DimensionResultRow;
  partly_allocated: number;
}

export const listDimensions = (companyId: number): Promise<Dimension[]> =>
  request<Dimension[]>(`/api/v1/companies/${companyId}/dimensjoner`);

export const getDimensionSuggestions = (companyId: number): Promise<DimensionSuggestions> =>
  request<DimensionSuggestions>(`/api/v1/companies/${companyId}/dimensjoner/forslag`);

export const addSuggestedDimensions = (
  companyId: number,
  kind: string,
  codes: string[],
): Promise<Dimension[]> =>
  json<Dimension[]>(`/api/v1/companies/${companyId}/dimensjoner/forslag`, 'POST', { kind, codes });

export const createDimension = (
  companyId: number,
  payload: { code: string; description: string },
): Promise<Dimension[]> =>
  json<Dimension[]>(`/api/v1/companies/${companyId}/dimensjoner`, 'POST', payload);

export const updateDimension = (
  companyId: number,
  dimensionId: number,
  payload: { description?: string; is_active?: boolean },
): Promise<Dimension[]> =>
  json<Dimension[]>(`/api/v1/companies/${companyId}/dimensjoner/${dimensionId}`, 'PATCH', payload);

export const deleteDimension = (companyId: number, dimensionId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/dimensjoner/${dimensionId}`, { method: 'DELETE' });

export const createDimensionValue = (
  companyId: number,
  dimensionId: number,
  payload: { value: string; description: string },
): Promise<Dimension[]> =>
  json<Dimension[]>(
    `/api/v1/companies/${companyId}/dimensjoner/${dimensionId}/verdier`,
    'POST',
    payload,
  );

export const updateDimensionValue = (
  companyId: number,
  valueId: number,
  payload: { description?: string; is_active?: boolean },
): Promise<Dimension[]> =>
  json<Dimension[]>(`/api/v1/companies/${companyId}/dimensjonsverdier/${valueId}`, 'PATCH', payload);

export const deleteDimensionValue = (companyId: number, valueId: number): Promise<void> =>
  request<void>(`/api/v1/companies/${companyId}/dimensjonsverdier/${valueId}`, {
    method: 'DELETE',
  });

export const getDimensionResult = (
  companyId: number,
  dimensionId: number,
  year: number,
): Promise<DimensionResult> =>
  request<DimensionResult>(
    `/api/v1/companies/${companyId}/dimensjoner/${dimensionId}/resultat?aar=${year}`,
  );

// --- Uttak, tap på krav og justering (mva-meldingens spesifikasjon) --------------

export type Spesifikasjon =
  | 'uttak'
  | 'tapPåKrav'
  | 'justering'
  | 'tilbakeføringAvInngåendeMerverdiavgift';

export interface Adjustment {
  id: number;
  voucher_no: string;
  occurred_on: string;
  spesifikasjon: Spesifikasjon;
  tax_code: string;
  /** Signed as the melding signs it: positive is owed, negative is credited. */
  merverdiavgift: string;
  grunnlag: string | null;
  account_number: string;
  account_description: string;
  description: string;
  is_reversal: boolean;
  reversed_by_id: number | null;
}

export interface AdjustmentList {
  year: number;
  kinds: { spesifikasjon: Spesifikasjon; tax_codes: string[]; default_account: string }[];
  entries: Adjustment[];
}

export const listAdjustments = (companyId: number, year: number): Promise<AdjustmentList> =>
  request<AdjustmentList>(`/api/v1/companies/${companyId}/mva/spesifikasjoner?aar=${year}`);

export const recordAdjustment = (
  companyId: number,
  payload: {
    spesifikasjon: Spesifikasjon;
    occurred_on: string;
    tax_code: string;
    amount: string;
    description: string;
    account_number?: string | null;
    party_id?: number | null;
  },
): Promise<Adjustment> =>
  json<Adjustment>(`/api/v1/companies/${companyId}/mva/spesifikasjoner`, 'POST', payload);

export const reverseAdjustment = (companyId: number, adjustmentId: number): Promise<Adjustment> =>
  json<Adjustment>(
    `/api/v1/companies/${companyId}/mva/spesifikasjoner/${adjustmentId}/reversering`,
    'POST',
    {},
  );

// --- Anlegg og avskrivning -------------------------------------------------------
//
// The anleggsregister and the year's depreciation voucher. The register is data
// that may be changed while its years are open; the voucher is review-then-post,
// corrected by reversal from its own routes like the opening balance.

export interface FixedAsset {
  company_id: number;
  id: number;
  description: string;
  saldogruppe: string;
  saldogruppe_label: string;
  rate_variant: string | null;
  account_number: string;
  acquired_on: string;
  cost: string;
  grant: string;
  grant_reduces_cost: boolean;
  voucher_id: number | null;
  building_id: number | null;
  opening_year: number | null;
  opening_value: string | null;
  useful_life_years: number | null;
  disposed_on: string | null;
  disposal_proceeds: string | null;
  note: string | null;
  saldo_key: string;
  created_at: string | null;
}

/** What the form sends. Amounts are decimal strings; null means "not given". */
export interface FixedAssetInput {
  description: string;
  saldogruppe: string;
  account_number: string | null;
  acquired_on: string | null;
  cost: string | null;
  grant: string;
  grant_reduces_cost: boolean;
  rate_variant: string | null;
  voucher_id: number | null;
  building_id: number | null;
  opening_value: string | null;
  useful_life_years: number | null;
  note: string | null;
}

export interface FixedAssetOpening {
  id: number;
  year: number;
  saldogruppe: string;
  amount: string;
  account_number: string | null;
  building_id: number | null;
}

export interface FixedAssetRegister {
  company_id: number;
  start_year: number | null;
  assets: FixedAsset[];
  openings: FixedAssetOpening[];
  groups: { code: string; label: string; rate: string | null; collective: boolean }[];
  variants: { code: string; label: string; rate: string }[];
  capitalisation_limit: string | null;
}

export interface SaldoYear {
  key: string;
  saldogruppe: string;
  label: string;
  asset_id: number | null;
  rate_variant: string | null;
  rate: string | null;
  opening: string;
  additions: string;
  grants: string;
  disposals: string;
  basis: string;
  max_depreciation: string;
  min_income: string;
  depreciation: string;
  income: string;
  to_gain_loss_account: string;
  closing: string;
  small_rest: boolean;
}

export interface SaldoSpecification {
  company_id: number;
  year: number;
  start_year: number;
  rules_from_year: number | null;
  rates_confirmed: boolean;
  posted: boolean;
  saldi: SaldoYear[];
  gain_loss_account: {
    opening: string;
    gains: string;
    losses: string;
    basis: string;
    income: string;
    deduction: string;
    closing: string;
  };
  unposted_years: number[];
}

export interface DepreciationVoucher {
  id: number;
  voucher_no: string;
  year: number;
  transaction_date: string;
  posted_at: string | null;
  is_reversal: boolean;
  reversed: boolean;
  corrects_id: number | null;
  total: string;
}

export interface DepreciationPlan {
  company_id: number;
  year: number;
  specification: SaldoSpecification;
  lines: (PlannedLine & { saldo_key: string; kind: string })[];
  total: string;
  postable: boolean;
  findings: YearFinding[];
  standing: DepreciationVoucher | null;
  history: DepreciationVoucher[];
}

const anleggPath = (companyId: number): string => `/api/v1/companies/${companyId}/anlegg`;

export const getFixedAssets = (companyId: number): Promise<FixedAssetRegister> =>
  request<FixedAssetRegister>(anleggPath(companyId));

export const createFixedAsset = (companyId: number, asset: FixedAssetInput): Promise<FixedAsset> =>
  json<FixedAsset>(anleggPath(companyId), 'POST', asset);

export const updateFixedAsset = (
  companyId: number,
  assetId: number,
  asset: Partial<FixedAssetInput>,
): Promise<FixedAsset> => json<FixedAsset>(`${anleggPath(companyId)}/${assetId}`, 'PATCH', asset);

export const deleteFixedAsset = (companyId: number, assetId: number): Promise<void> =>
  request<void>(`${anleggPath(companyId)}/${assetId}`, { method: 'DELETE' });

export const disposeFixedAsset = (
  companyId: number,
  assetId: number,
  disposedOn: string,
  proceeds: string,
): Promise<FixedAsset> =>
  json<FixedAsset>(`${anleggPath(companyId)}/${assetId}/avgang`, 'POST', {
    disposed_on: disposedOn,
    proceeds,
  });

export const undoFixedAssetDisposal = (companyId: number, assetId: number): Promise<FixedAsset> =>
  request<FixedAsset>(`${anleggPath(companyId)}/${assetId}/avgang`, { method: 'DELETE' });

export const setFixedAssetOpenings = (
  companyId: number,
  year: number,
  rows: { saldogruppe: string; amount: string; account_number: string | null; building_id: number | null }[],
): Promise<FixedAssetOpening[]> =>
  json<FixedAssetOpening[]>(`${anleggPath(companyId)}/inngaende-saldo`, 'PUT', { aar: year, rows });

export const getDepreciation = (companyId: number, year: number): Promise<DepreciationPlan> =>
  request<DepreciationPlan>(`${anleggPath(companyId)}/avskrivning?aar=${year}`);

/** The review with the person's own amounts per saldo key. Stores nothing. */
export const reviewDepreciation = (
  companyId: number,
  year: number,
  choices: Record<string, string>,
): Promise<DepreciationPlan> =>
  json<DepreciationPlan>(`${anleggPath(companyId)}/avskrivning/kontroll`, 'POST', {
    aar: year,
    valg: choices,
  });

/** Posts the year's depreciation; `correct` reverses the standing voucher and replaces it. */
export const postDepreciation = (
  companyId: number,
  year: number,
  choices: Record<string, string>,
  correct: boolean,
): Promise<PostedYearVoucher> =>
  json<PostedYearVoucher>(`${anleggPath(companyId)}/avskrivning`, 'POST', {
    aar: year,
    valg: choices,
    korriger: correct,
  });

export const reverseDepreciation = (companyId: number, year: number): Promise<PostedYearVoucher> =>
  json<PostedYearVoucher>(`${anleggPath(companyId)}/avskrivning/reversering`, 'POST', { aar: year });

// --- Tilbakemelding: feedback from anybody, kept without a name --------------------
//
// Public routes that ask nobody who is calling (docs/feedback.md). The cookie
// goes along like on every request and is not read; the reference in the
// answer is the only way back to the item, so the page shows it once and
// leaves keeping it to the person.

export interface FeedbackSent {
  /** Shown this once and never again: only its digest is stored. */
  reference: string;
  number: number;
  status: FeedbackStatus;
  created_at: string;
  /** /tilbakemelding/#ref=…, where the person follows it. */
  url: string;
}

export interface FeedbackItem {
  number: number;
  kind: FeedbackKind;
  source: 'person' | 'agent';
  text: string;
  page: string | null;
  status: FeedbackStatus;
  reply: string | null;
  published: boolean;
  created_at: string;
  changed_at: string | null;
}

/** On the public list: the operator's own words, never the sender's. */
export interface PublishedFeedback {
  number: number;
  kind: FeedbackKind;
  summary: string;
  status: FeedbackStatus;
  reply: string | null;
  received_on: string;
  changed_at: string | null;
}

const FEEDBACK = '/api/v1/tilbakemeldinger';

/** `source` stays the API's default, `person`: whoever presses the button on the page is one. */
export const sendFeedback = (fields: {
  kind: FeedbackKind;
  text: string;
  page: string | null;
}): Promise<FeedbackSent> => json<FeedbackSent>(FEEDBACK, 'POST', fields);

export const getFeedback = (reference: string): Promise<FeedbackItem> =>
  request<FeedbackItem>(`${FEEDBACK}/${encodeURIComponent(reference)}`);

export const withdrawFeedback = (reference: string): Promise<void> =>
  request<void>(`${FEEDBACK}/${encodeURIComponent(reference)}`, { method: 'DELETE' });

export const listPublishedFeedback = (): Promise<PublishedFeedback[]> =>
  request<PublishedFeedback[]>(FEEDBACK);
