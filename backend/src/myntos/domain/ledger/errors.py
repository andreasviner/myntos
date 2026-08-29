"""Refusals from the accounting core.

Same shape as :mod:`myntos.domain.identity.errors`: meaning here, status codes
in :mod:`myntos.api.errors`. Messages are Norwegian because they are shown to
the user — the bookkeeper is the one who has to act on them.

:class:`~myntos.domain.ledger.posting.PostingError` stays separate and is not
part of this hierarchy. It marks a violated ledger invariant, which by the time
a request reaches it is a bug in *our* code rather than bad input: everything
the user can get wrong is rejected here first.
"""


class LedgerError(Exception):
    """Base class for every refusal from the accounting core."""


class CompanyNotFoundError(LedgerError):
    """No such company, or none the caller may see.

    One error for both, so the API cannot be used to probe for company ids
    belonging to other organizations.
    """


class InvalidOrgnrError(LedgerError):
    """The organization number is not nine digits with a valid check digit."""


class DuplicateCompanyError(LedgerError):
    """This organization already keeps books for that orgnr."""


class CompanyAlreadyClaimedError(LedgerError):
    """Somebody else has already confirmed ownership of this orgnr.

    Raised only when confirming, never when creating: a second set of books for
    the same client is legitimate (see :class:`~myntos.domain.ledger.models.Company`),
    a second *confirmed* one is not. The message says what the person can
    actually do about it — ask the organization that holds the claim, or keep
    the unconfirmed books they just made — and deliberately does not name that
    organization, which they have no membership in.
    """


class NotAuthorizedForPartyError(LedgerError):
    """Altinn does not list this orgnr among the parties the person may act for.

    The whole point of the confirmation: the evidence comes from Altinn's access
    management rather than from the person asserting it.
    """


class InvalidBankAccountError(LedgerError):
    """The bank account number is not eleven digits with a valid check digit.

    Refused rather than stored as typed because the number reaches the outside
    world on every invoice, and an issued invoice cannot be corrected — see
    :mod:`myntos.domain.ledger.bankkonto`.
    """


class UnknownCashFlowTypeError(LedgerError):
    """The requested cash-flow category is not in the catalogue."""


class UnknownTaxCodeError(LedgerError):
    """The requested VAT code is not set up for this company."""


class InvalidAmountError(LedgerError):
    """The amount is zero, negative, or carries fractions of an øre."""


class ClosedFinancialYearError(LedgerError):
    """The financial year is closed; nothing more may be posted into it."""


class PeriodOutsideBooksError(LedgerError):
    """The date precedes the company's first financial year in Myntos.

    Not a formality: the VAT rates seeded for a company are only claimed to
    hold from that year onwards, and posting earlier would snapshot a rate onto
    the entry that nobody ever asserted was correct.
    """


class PartyNotFoundError(LedgerError):
    """No such customer or supplier in this company."""


class DuplicatePartyError(LedgerError):
    """That customer or supplier number is already in use."""


class ChartIncompleteError(LedgerError):
    """The company's kontoplan is missing an account the operation requires.

    Only reachable for a company created before an account joined the seed
    chart, or one edited by hand. Named rather than left as a ``KeyError`` so
    the answer says which account is missing.
    """


class VoucherNotFoundError(LedgerError):
    """No such voucher in this company."""


class AlreadyReversedError(LedgerError):
    """The voucher has already been reversed once."""
