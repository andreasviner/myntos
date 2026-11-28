"""Domain errors for the shareholder register and RF-1086 filing."""

from myntos.domain.ledger.errors import LedgerError


class AksjonaerError(LedgerError):
    """Base error for shareholder register and RF-1086 failures."""


class ShareClassNotFoundError(AksjonaerError):
    """The named share class does not exist in this company's register."""


class DuplicateShareClassError(AksjonaerError):
    """The company already has a share class with this code."""


class ShareClassInUseError(AksjonaerError):
    """The share class has events or filings and cannot be deleted."""


class ShareholderNotFoundError(AksjonaerError):
    """The named shareholder does not exist in this company's register."""


class ShareholderInUseError(AksjonaerError):
    """The shareholder appears on events and cannot be deleted."""


class ShareEventNotFoundError(AksjonaerError):
    """The named corporate or shareholder event does not exist in this company."""


class AksjonaerFilingNotFoundError(AksjonaerError):
    """The named RF-1086 filing does not exist in this company."""


class AksjonaerDocumentNotFoundError(AksjonaerError):
    """The named archived RF-1086 document does not exist on this filing."""


class InvalidAksjonaerDataError(AksjonaerError):
    """Invalid shareholder identifier, share class code, or event payload."""


class AksjonaerNotReadyError(AksjonaerError):
    """Pre-flight checks found blocking errors on the RF-1086 statement."""


class AksjonaerConfirmationRequiredError(AksjonaerError):
    """The caller did not explicitly confirm sending RF-1086 to Skatteetaten."""


class AksjonaerSubmissionNotAvailableError(AksjonaerError):
    """Skatteetaten RF-1086 submission is not configured on this deployment."""


class AksjonaerPrefillNotFoundError(AksjonaerError):
    """Skatteetaten has no previous RF-1086 filing available to prefill from."""


class AksjonaerRemoteError(AksjonaerError):
    """Skatteetaten or Maskinporten refused or failed the RF-1086 call."""
