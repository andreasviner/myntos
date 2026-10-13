"""Refusals from the manual voucher.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, so the ledger's
handler renders them; the status codes are in :mod:`myntos.api.errors`. The
refusals a manual voucher shares with every other voucher (a closed year, a
voucher already reversed, a reversal reversed) are the ledger's own errors,
raised as they are, so an agent meets one code for one thing wherever it posts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from myntos.domain.ledger.errors import LedgerError, VoucherNotFoundError

if TYPE_CHECKING:
    from myntos.domain.arsoppgjor.findings import Finding


class InvalidManualVoucherError(LedgerError):
    """The voucher cannot be posted as it is; the findings say why.

    Carries the whole list, like the opening balance: a voucher with three
    unknown accounts and no reason should say so once, not one at a time.
    """

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class ManualVoucherNotFoundError(VoucherNotFoundError):
    """No manual voucher with that id in this company's books.

    A subclass of the ledger's own not-found, so it is the same 404: a payment's
    id sent to the manual voucher's routes is as unknown there as another
    company's, and the routes of each journal cannot be used on the other's.
    """
