"""Refusals from bank import and reconciliation.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, for the reason
the bilag archive's are: one hierarchy is one status table in
:mod:`myntos.api.errors`, and a refused statement is an accounting-core refusal.
Messages are Norwegian because the bookkeeper is the one who acts on them.
"""

from myntos.domain.ledger.errors import LedgerError


class BankStatementError(LedgerError):
    """Base class for everything the bank import refuses."""


class InvalidBankStatementError(BankStatementError):
    """The file is not a camt.053 statement we can read.

    Covers malformed XML, a document type that is not camt.053, a DTD (which a
    bank statement never carries and an attack always does), and a statement
    missing the fields the import cannot do without.
    """


class UnbalancedBankStatementError(BankStatementError):
    """The statement's own arithmetic does not hold.

    Opening balance plus the booked entries must equal the closing balance. A
    file that disagrees with itself is truncated, edited or mis-exported, and
    importing the lines it does contain would reconcile the register against an
    account that never looked like that.
    """


class UnsupportedStatementCurrencyError(BankStatementError):
    """An entry, or the account, is in something other than kroner.

    The ledger is carried in NOK and the register has no way to post a
    currency movement from a bank line yet. Refused whole rather than skipped,
    because a statement imported without its currency lines no longer adds up.
    """


class UnknownStatementAccountError(BankStatementError):
    """The statement is for an account the company has not registered.

    Refused rather than added on the fly. Which accounts belong to a company is
    a statement about the company, edited on /organisasjon/ with
    ``organization:manage``, and a file somebody uploads is not evidence that
    an account is theirs: the wrong client's statement is exactly the file that
    would arrive here by mistake.
    """


class BankStatementConflictError(BankStatementError):
    """A bank reference we already hold arrives with different contents.

    The bank's reference is the dedup key, so the same reference is the same
    booking. One that now says another amount or date is either a bank
    correcting history or two different files being mixed up, and neither is
    something to resolve by keeping whichever arrived first without saying so.
    """


class BankStatementNotFoundError(BankStatementError):
    """No such statement on this company. Same answer for one next door."""


class BankLineNotFoundError(BankStatementError):
    """No such bank line on this company. Same answer for one next door."""


class BankLineAlreadyMatchedError(BankStatementError):
    """The bank line is already reconciled against a voucher."""


class VoucherAlreadyMatchedError(BankStatementError):
    """The voucher is already reconciled against another bank line.

    One movement on the account is one voucher. Two bank lines on the same
    voucher would let a single booking explain two movements of money, and the
    register would look reconciled while the account held twice the difference.
    """


class BankMatchMismatchError(BankStatementError):
    """The voucher cannot be the booking of this bank line.

    It moves a different amount, goes the other way, never touched the bank
    account, or has been reversed. Confirming it anyway would mark the line
    reconciled against an entry that does not explain it.
    """
