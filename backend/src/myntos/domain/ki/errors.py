"""Why a KI plan, subscription, invoice, or chat action was refused.

The HTTP status codes live in :mod:`myntos.api.errors`.
"""


class KiError(Exception):
    """Base for KI plans, billing, and chat."""


class KiPlanNotFoundError(KiError):
    """No plan by that code. 404, like any other name that is not there."""


class KiPaymentNotFoundError(KiError):
    """No checkout by that reference in this company. 404, so references cannot be probed."""


class KiSubscriptionNotFoundError(KiError):
    """This company has no active KI subscription to cancel or inspect."""


class KiInvoiceNotFoundError(KiError):
    """No KI billing invoice by that id in this company. 404."""


class KiChatThreadNotFoundError(KiError):
    """No KI chat thread by that id in this company. 404."""


class KiPurchaseNotAvailableError(KiError):
    """This deployment cannot sell a plan: no Mollie key for the mode, or no Løynd key."""


class KiBillingNotConfiguredError(KiError):
    """Monthly invoicing for KI subscriptions is not configured on this deployment."""


class KiTurnedOffError(KiError):
    """The company has switched KI off, so paid plans and KI chat cannot run."""


class KiPaymentProviderError(KiError):
    """Mollie refused or did not answer. 502: the request was fine and so are we."""


class KiModelUnavailableError(KiError):
    """Løynd could not answer just now. 502."""
