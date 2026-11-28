"""Domain errors for email invoice intake and company inbox addresses."""


class MottakError(Exception):
    """Base error for incoming email invoice operations."""


class InboxNotAvailableError(MottakError):
    """Inbound email intake is not configured on this Myntos deployment."""


class InvalidWebhookSignatureError(MottakError):
    """Inbound email webhook request failed HMAC signature or timestamp verification."""


class InboundEmailRateLimitedError(MottakError):
    """The company's email inbox exceeded its hourly incoming email limit."""


class InvalidInboundEmailError(MottakError):
    """The inbound email payload was missing required headers or could not be parsed."""


class InvalidAllowedSenderError(MottakError):
    """An entry in the company's approved-sender allowlist was malformed."""


class MailDocumentNotFoundError(MottakError):
    """The incoming email document does not exist or belongs to another company."""


class MailDocumentAlreadyBookedError(MottakError):
    """The incoming email document has already been booked as a voucher."""


class MailDocumentDismissedError(MottakError):
    """The incoming email document was dismissed and must be reopened before booking."""


class MailDocumentInvalidError(MottakError):
    """The booking parameters for an incoming email document were invalid."""
