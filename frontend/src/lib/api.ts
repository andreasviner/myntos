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
 */

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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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

export interface Membership {
  organization_id: number;
  organization_name: string;
  organization_slug: string;
  /**
   * True while the name is one signup invented ("Ola Nordmann (privat)") rather
   * than one anyone chose. Worth asking about; not worth presenting as settled.
   */
  organization_name_is_placeholder: boolean;
  plan: string;
  role: string;
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

/** Where to send the browser to start a federated sign-in flow. */
export const providerStartUrl = (code: string, redirectTo: string): string =>
  `${API_BASE_URL}/api/v1/auth/${encodeURIComponent(code)}/start` +
  `?redirect_to=${encodeURIComponent(redirectTo)}`;

// --- Companies ------------------------------------------------------------------

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
   * Eleven digits, no separators. Null until someone fills it in, and an
   * invoice cannot be issued while it is, because there would be nowhere to pay
   * it (see the `seller_bank_account` violation).
   */
  bank_account: string | null;
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

/** Correct the company's own details. Every omitted field is left alone. */
export const updateCompany = (
  companyId: number,
  payload: {
    name?: string;
    address_line?: string | null;
    postal_code?: string | null;
    city?: string | null;
    vat_registered?: boolean;
    in_foretaksregisteret?: boolean;
    organisation_form?: string | null;
    bank_account?: string | null;
    ki_enabled?: boolean;
  },
): Promise<Company> => json<Company>(`/api/v1/companies/${companyId}`, 'PATCH', payload);

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
  request<Company>(`/api/v1/companies/${companyId}/bekreft`, { method: 'POST' });

/** The code a 409 from `verifyCompany` carries when somebody claimed it first. */
export const ALREADY_CLAIMED = 'company_already_claimed';

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

/** A company after a register refresh, and everything the register knows. */
export interface CompanyRefresh {
  company: Company;
  /** Which stored fields the register moved. Empty when it agreed with all. */
  changed: string[];
  unit: RegisteredUnit;
  roles: RegisteredRole[];
}

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
  request<CompanyRefresh>(`/api/v1/companies/${companyId}/hent`, { method: 'POST' });

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
}

export interface Invitation {
  id: number;
  email: string;
  role: string;
  expires_at: string;
  /** Only ever populated on the response that created it, since the token is stored hashed. */
  invite_url: string | null;
}

export const listMembers = (organizationId: number): Promise<Member[]> =>
  request<Member[]>(`/api/v1/organizations/${organizationId}/members`);

export const listInvitations = (organizationId: number): Promise<Invitation[]> =>
  request<Invitation[]>(`/api/v1/organizations/${organizationId}/invitations`);

export const inviteMember = (
  organizationId: number,
  email: string,
  role: string,
): Promise<Invitation> =>
  json<Invitation>(`/api/v1/organizations/${organizationId}/invitations`, 'POST', { email, role });

export const revokeInvitation = (organizationId: number, invitationId: number): Promise<void> =>
  request<void>(`/api/v1/organizations/${organizationId}/invitations/${invitationId}`, {
    method: 'DELETE',
  });

export const changeMemberRole = (
  organizationId: number,
  userId: number,
  role: string,
): Promise<Member> =>
  json<Member>(`/api/v1/organizations/${organizationId}/members/${userId}`, 'PATCH', { role });

export const removeMember = (organizationId: number, userId: number): Promise<void> =>
  request<void>(`/api/v1/organizations/${organizationId}/members/${userId}`, { method: 'DELETE' });

export const renameOrganization = (organizationId: number, name: string): Promise<unknown> =>
  json<unknown>(`/api/v1/organizations/${organizationId}`, 'PATCH', { name });

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
}

export const getCatalogue = (): Promise<Catalogue> => request<Catalogue>('/api/v1/cashflow/types');

/** Read a PDF and get the entry form filled in. Stores nothing. */
export const interpretDocument = (companyId: number, file: File): Promise<InvoiceSuggestion> => {
  const form = new FormData();
  form.append('file', file);
  return request<InvoiceSuggestion>(`/api/v1/companies/${companyId}/cashflow/forslag`, {
    method: 'POST',
    body: form,
  });
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
  seller_bank_account: string | null;

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
  request<void>(`/api/v1/companies/${companyId}/faktura/${documentId}`, { method: 'DELETE' });

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
