"""Refusals from the anleggsregister and the year's depreciation.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, like the
årsoppgjør ones, so the ledger's handler renders them; the status codes are in
:mod:`myntos.api.errors`. Messages are Norwegian because the person keeping the
register is the one who has to act on them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from myntos.domain.ledger.errors import LedgerError

if TYPE_CHECKING:
    from myntos.domain.arsoppgjor.findings import Finding


class AnleggError(LedgerError):
    """Base class for every refusal in this package."""


class FixedAssetNotFoundError(AnleggError):
    """No such asset in this company's register (another company's is the same answer)."""


class InvalidFixedAssetError(AnleggError):
    """The asset as sent cannot be registered: a field is missing, wrong or contradicts another."""


class RegisterYearClosedError(AnleggError):
    """The change would alter a saldo in a closed year.

    A closed year's saldo is what its skattemelding was filed from. Changing
    the register behind it would make the specification Myntos prints for that
    year disagree with the one that was filed, with nothing to say why.
    """


class InvalidOpeningSaldoError(AnleggError):
    """The opening saldi cannot be stored: wrong year, unknown group, or an account that is not an asset."""


class DepreciationNotPostableError(AnleggError):
    """The year's depreciation cannot be posted; the findings say why."""

    def __init__(self, message: str, findings: list[Finding]) -> None:
        super().__init__(message)
        self.findings = findings


class DepreciationExistsError(AnleggError):
    """The year's depreciation is posted already, and the request did not ask to correct it."""


class NoDepreciationError(AnleggError):
    """There is no standing depreciation for the year to reverse or correct."""
