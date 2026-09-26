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

/** Where to send the browser to start a federated sign-in flow. */
export const providerStartUrl = (code: string, redirectTo: string): string =>
  `${API_BASE_URL}/api/v1/auth/${encodeURIComponent(code)}/start` +
  `?redirect_to=${encodeURIComponent(redirectTo)}`;

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
 * - **409 `company_already_claimed`** — another organization confirmed this
 *   orgnr first. Show the message and the way out: ask an administrator there,
 *   or keep these books unconfirmed.
 */
export const verifyCompany = (companyId: number): Promise<Company> =>
  request<Company>(`/api/v1/companies/${companyId}/bekreft`, {
    method: 'POST',
  });

/** The code a 409 from `verifyCompany` carries when somebody claimed it first. */
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
   * orgnr, so confirming it here would be refused.
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
}

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
  attachments: AttachmentInfo[];
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
  output_vat: string;
  input_vat: string;
  vat_position: string;
  cash_position: string;
  by_type: TypeTotal[];
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
   * What the register still needs to know, phrased so it can be answered in the
   * explanation box. Answering and reading again produces a better proposal.
   */
  questions: SuggestionQuestion[];
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
}

/** One uploaded file, and how much text came out of it. */
export interface DocumentRead {
  filename: string;
  content_type: string;
  /** Zero means nothing was read from this one — a blurred photo, a blank page. */
  chars: number;
}

export const getCatalogue = (): Promise<Catalogue> => request<Catalogue>('/api/v1/cashflow/types');

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
 * the paperwork looks identical in all three cases. It is shown to the model and
 * stored nowhere.
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
): Promise<InvoiceSuggestion> => {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  if (forklaring !== undefined && forklaring.trim() !== '') {
    form.append('forklaring', forklaring.trim());
  }
  return request<InvoiceSuggestion>(`/api/v1/companies/${companyId}/cashflow/forslag`, {
    method: 'POST',
    body: form,
  });
};

/** What the streaming reader reports on its way to a proposal. */
export interface InterpretHandlers {
  /** Which files were read, and how much text came out of each. Arrives first. */
  onDocuments?: (documents: DocumentRead[]) => void;
  /** The next piece of the description, as the model writes it. */
  onDescription?: (piece: string) => void;
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

export const fakturaEhfUrl = (companyId: number, documentId: number): string =>
  `${API_BASE_URL}/api/v1/companies/${companyId}/faktura/${documentId}/ehf`;
