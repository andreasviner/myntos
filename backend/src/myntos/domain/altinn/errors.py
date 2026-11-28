"""Domain errors for Altinn 3 systembruker management."""

from myntos.domain.ledger.errors import LedgerError


class SystemUserNotAvailableError(LedgerError):
    """Maskinporten or Altinn vendor settings are not configured on this deployment."""


class SystemUserRequestNotFoundError(LedgerError):
    """No Altinn system-user request exists for this company."""


class SystemUserNotApprovedError(LedgerError):
    """The company has not yet approved a system user in Altinn for this resource."""


class SystemUserRemoteError(LedgerError):
    """Altinn or Maskinporten refused or could not complete the system-user call."""


class InvalidSystemUserResourceError(LedgerError):
    """A requested Altinn resource is outside what Myntos declares in the system register."""
