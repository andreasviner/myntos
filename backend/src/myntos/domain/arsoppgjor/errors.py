"""Refusals from the opening balance and from årsavslutning.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, like the MVA
ones, so the ledger's handler renders them; the status codes are in
:mod:`myntos.api.errors`. Messages are Norwegian because the person closing the
books is the one who has to act on them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from myntos.domain.ledger.errors import LedgerError

if TYPE_CHECKING:
    from myntos.domain.arsoppgjor.findings import Finding


class ArsoppgjorError(LedgerError):
    """Base class for every refusal in this package."""


class OpeningBalanceNotAllowedError(ArsoppgjorError):
    """The company's books cannot take an opening balance in this state.

    Either the year is not the first one the company keeps in Myntos (a later
    year's opening balance *is* the year before's closing, and a second one would
    count it twice), or the year is closed.
    """


class OpeningBalanceExistsError(ArsoppgjorError):
    """An opening balance is already posted, and the request did not ask to correct it.

    Kept apart from a silent replacement on purpose: posting a second opening
    voucher on top of the first would double every balance, and replacing it
    is a correction the person has to mean.
    """


class NoOpeningBalanceError(ArsoppgjorError):
    """There is no standing opening balance to reverse or correct."""


class InvalidOpeningBalanceError(ArsoppgjorError):
    """The lines do not make an opening balance that can be posted.

    Carries the findings, so the API can answer with the list rather than with
    the first problem only: a saldobalanse with three unknown accounts should
    say so once, not three times in a row.
    """

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class InvalidSaldobalanseError(ArsoppgjorError):
    """The uploaded file is not a saldobalanse this reader can make sense of."""


class YearNotClosableError(ArsoppgjorError):
    """The year cannot be closed yet; the findings say why."""

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class YearNotReopenableError(ArsoppgjorError):
    """The year cannot be reopened: it is open, or a later year is closed."""


class ReopeningReasonRequiredError(ArsoppgjorError):
    """Reopening a closed year needs a written reason and an explicit confirmation."""


class FinancialYearNotFoundError(ArsoppgjorError):
    """The company keeps no such financial year in Myntos."""


# --- The farm's count and private uttak (landbruk) -----------------------------------
#
# Two bases carry the status codes (myntos.api.errors), and each refusal is its
# own subclass so the ``code`` an agent branches on says which list and why.


class FarmListConflictError(ArsoppgjorError):
    """Refused by the state the farm's list is in, not by what was sent (409)."""


class FarmListInvalidError(ArsoppgjorError):
    """What was sent cannot be posted as it is; carries the findings (422)."""

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class CountExistsError(FarmListConflictError):
    """A count for the year stands, and the request did not ask to correct it.

    Kept apart from a silent replacement for the reason the opening balance
    gives: two counts would move 1450 twice, and replacing one is a correction
    the person has to mean.
    """


class NoCountError(FarmListConflictError):
    """There is no standing count for the year to correct or withdraw."""


class InvalidCountError(FarmListInvalidError):
    """The count cannot be posted; the findings say why."""


class UttakExistsError(FarmListConflictError):
    """A list of private uttak for the year stands, and the request did not ask to correct it."""


class NoUttakError(FarmListConflictError):
    """There is no standing list of private uttak for the year to correct or withdraw."""


class InvalidUttakError(FarmListInvalidError):
    """The list of private uttak cannot be posted; the findings say why."""
