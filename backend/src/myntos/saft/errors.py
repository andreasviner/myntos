"""Refusals from the SAF-T export, and from reading another program's SAF-T file.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError` so the ledger's
handler renders them; the status codes are in :mod:`myntos.api.errors`.
Messages are Norwegian because the person exporting is the one who has to act.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from myntos.domain.ledger.errors import LedgerError

if TYPE_CHECKING:
    from myntos.domain.arsoppgjor.findings import Finding as ImportFinding
    from myntos.saft.check import Finding


class SaftError(LedgerError):
    """Base class for every refusal about a SAF-T export."""


class SaftYearNotFoundError(SaftError):
    """The company has no books for the year asked for."""


class UnsupportedSaftVersionError(SaftError):
    """A version the year may not be exported in, such as v1.30 for 2027."""


class SaftNotExportableError(SaftError):
    """The cross-reference check found something that would fail at the auditor.

    Carries the findings so the route can say what they are; the message alone
    only says that there are some.
    """

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class SaftImportError(SaftError):
    """Base class for a SAF-T file that cannot be read into these books."""


class InvalidSaftFileError(SaftImportError):
    """The upload is not a SAF-T Financial file this reader can make sense of."""


class SaftWrongCompanyError(SaftImportError):
    """The file is another company's books.

    Refused outright rather than reported as a finding: there is nothing in
    another company's balances that belongs in this one's opening balance, and
    showing them as lines to edit would invite exactly the mistake.
    """


class SaftWrongYearError(SaftImportError):
    """The file covers a year that says nothing about this opening balance."""


class InvalidPrintoutError(SaftImportError):
    """The upload is not a printout of vouchers with attachments this reader knows.

    The documents behind the vouchers come from the old program as its own
    printout (:mod:`myntos.saft.printout`), not in the SAF-T file.
    """


class PrintoutWrongCompanyError(SaftImportError):
    """The printout is another company's vouchers.

    Refused whole, like another company's SAF-T file: a document from another
    set of books has no voucher in this one to belong to, and one that happened
    to share a number would be put on the wrong voucher for good.
    """


class InvalidVoucherImportError(SaftImportError):
    """The year's vouchers cannot be posted from the file yet.

    Carries the review's findings, the way an opening balance's refusal does:
    which accounts and parties to add first, which vouchers do not balance.
    """

    def __init__(self, message: str, findings: list[ImportFinding]) -> None:
        super().__init__(message)
        self.findings = findings
