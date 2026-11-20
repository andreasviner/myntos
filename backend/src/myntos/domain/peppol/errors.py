"""Refusals from sending over Peppol.

Meaning here, status codes in :mod:`myntos.api.errors`, Norwegian messages
because the person who has to act on them is the one sending the invoice. Each
message says what to do instead, because the usual answer to "this cannot go as
an EHF" is the PDF, sent by the person themselves.
"""

from __future__ import annotations


class PeppolError(Exception):
    """Base class for every refusal from sending over Peppol."""


class PeppolNotAvailableError(PeppolError):
    """This deployment has no access point to send through."""


class CompanyNotConfirmedError(PeppolError):
    """The company has not been confirmed through Altinn.

    The access point takes whatever company the deployment's key names, so
    without this anybody who typed an orgnr into a new account could send
    invoices in that business's name. Confirming is what proves they may.
    """


class CompanyAddressMissingError(PeppolError):
    """The access point needs the registered address, and the company has none."""


class NotRegisteredError(PeppolError):
    """The company is not registered for e-invoicing, or has not finished the check."""


class NotSendableError(PeppolError):
    """This document cannot go as an EHF: a draft, or a buyer with no orgnr."""


class AlreadySentError(PeppolError):
    """The document went once and did not fail, so sending it again would duplicate it."""


class RecipientNotOnPeppolError(PeppolError):
    """The customer cannot receive EHF over Peppol. Nothing was sent."""


class AccessPointRefusedError(PeppolError):
    """The access point refused the EHF or the registration, and said why.

    ``problems`` carries its findings. The XML and the company's details are
    Myntos's own, so a refusal is ours to fix, and the findings are what the
    person passes on.
    """

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class SendUncertainError(PeppolError):
    """The send was made and no answer came, so nobody knows whether it went."""


class AccessPointUnavailableError(PeppolError):
    """The access point could not be reached or did not answer as expected."""


class TransmissionNotFoundError(PeppolError):
    """The document has never been sent over Peppol."""
