"""Domain errors for årsregnskap (RR-0002) preparation and filing.

Subclasses :class:`~myntos.domain.ledger.errors.LedgerError` so
:mod:`myntos.api.errors` maps every error uniformly to ``{"detail", "code"}``.
"""

from __future__ import annotations

from myntos.domain.ledger.errors import LedgerError


class AarsregnskapError(LedgerError):
    """Base error for annual accounts (årsregnskap) operations."""


class AarsregnskapFilingNotFoundError(AarsregnskapError):
    """No årsregnskap filing with that id belongs to this company."""


class AarsregnskapDocumentNotFoundError(AarsregnskapError):
    """No archived document of that type exists on the årsregnskap filing."""


class AarsregnskapNotReadyError(AarsregnskapError):
    """Blocking findings prevent preparing or submitting the årsregnskap."""


class AarsregnskapChangedError(AarsregnskapError):
    """The books or draft moved since the user confirmed the figures."""


class AarsregnskapConfirmationRequiredError(AarsregnskapError):
    """The caller did not explicitly confirm the figures before locking or signing."""


class AarsregnskapNotSubmittableError(AarsregnskapError):
    """The filing is not in a state that allows the requested transition."""


class InvalidAarsregnskapDataError(AarsregnskapError):
    """Input on the draft, notes, or prior-year figures is invalid."""


class AarsregnskapSubmissionNotAvailableError(AarsregnskapError):
    """Altinn 3 årsregnskap app URL or authentication is not configured."""


class AarsregnskapRemoteError(AarsregnskapError):
    """Altinn 3 or Regnskapsregisteret refused the request or could not be reached."""
