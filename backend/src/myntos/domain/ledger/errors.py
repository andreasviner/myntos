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


class CompanyOrgnrLockedError(LedgerError):
    """The orgnr cannot be changed, because ownership has been confirmed.

    A typed orgnr is a claim nobody has checked, and correcting a typo in one is
    an ordinary thing to want. A confirmed one is different: somebody proved
    through Altinn that they may act for exactly that number, and the proof
    belongs to the number rather than to the row. Letting it move would carry
    the confirmation across to a company nobody has shown any right to.
    """


class CompanyAlreadyClaimedError(LedgerError):
    """Somebody else has already confirmed ownership of this orgnr.

    A question rather than a wall. Raised only when confirming, and only until
    the person says they want to go on anyway (``even_if_claimed``): a second
    confirmed set of books is allowed, but nobody should end up with one
    without knowing the first exists, because both can file for the company.
    The message says what the person can do about it, ask the organization
    that holds the claim or confirm anyway, and deliberately does not name
    that organization, which they have no membership in.
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


class BankAccountNotFoundError(LedgerError):
    """No such bank account on this company.

    The same answer for an account that does not exist and one belonging to a
    company next door, for the reason :class:`CompanyNotFoundError` gives: these
    ids are sequential, and a 403 would say which of them are real.
    """


class DuplicateBankAccountError(LedgerError):
    """The company already has that kontonummer.

    Per company, not globally — two sets of books may name the same account, and
    the ENK that became an AS is the ordinary case. What is refused here is the
    same number twice on one company, which is never a second account: it is a
    second row that makes «which one is this» unanswerable.
    """


class PrimaryBankAccountRequiredError(LedgerError):
    """The primary account cannot simply be un-marked.

    A company that has accounts at all has one an invoice is paid to by default,
    and clearing the flag would leave "where does this get paid" with no answer
    while every account on screen still looks fine. Moving it is a promotion:
    mark another account primary and this one stops being it.
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


class CannotReverseHereError(LedgerError):
    """The voucher belongs to a flow that corrects it in its own way.

    An opening balance is replaced through its own page, and a year-end closing
    is undone only by reopening the year, which records who did it and why. A
    voucher brought from the previous program is corrected in that program.
    """


# --- The kontoplan ----------------------------------------------------------------


class AccountNotFoundError(LedgerError):
    """No such account in this company's kontoplan."""


class InvalidAccountNumberError(LedgerError):
    """Not a four-digit NS 4102 number, or a type the number's class cannot have."""


class DuplicateAccountError(LedgerError):
    """The company's kontoplan already has an account with that number."""


class InactiveAccountError(LedgerError):
    """The account is deactivated, so nothing new may be posted to it."""


class AccountInUseError(LedgerError):
    """The account cannot be deactivated or deleted.

    Either the posting logic refers to it by number (the bank, the VAT
    accounts, the party balance accounts), or it carries history: postings,
    an opening balance, a customer or supplier settling against it.
    """


class InvalidGroupingError(LedgerError):
    """The næringsspesifikasjon pair is not in Skatteetaten's published list."""


class TaxCodeNotAllowedError(LedgerError):
    """The VAT code is not one this company may use on that date.

    An unregistered business claiming a deduction, or charging output VAT,
    is the case this exists for: see :mod:`myntos.domain.ledger.vatstatus`.
    """


# --- Composite entries and add-ons ----------------------------------------------


class InvalidCompositeEntryError(LedgerError):
    """A composite entry that cannot be posted as one: too few or too many lines,
    or lines that cancel out so that no money moved at all.

    A composite entry is one bank movement split into the trades behind it. With
    one line it is an ordinary entry, which has its own route; with lines that
    net to nothing there is no bank movement for it to describe.
    """


class SegmentNotEnabledError(LedgerError):
    """The category belongs to an add-on these books do not have switched on.

    Refused rather than switching the add-on on, because one entry must not
    change what every colleague sees: see :mod:`myntos.domain.ledger.landbruk`.
    """
