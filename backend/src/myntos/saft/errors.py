"""Refusals from the SAF-T export.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError` so the ledger's
handler renders them; the status codes are in :mod:`myntos.api.errors`.
Messages are Norwegian because the person exporting is the one who has to act.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from myntos.domain.ledger.errors import LedgerError

if TYPE_CHECKING:
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
