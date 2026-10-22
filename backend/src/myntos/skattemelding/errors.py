"""Refusals from the skattemelding.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, like the
MVA-melding's, so the ledger's handler renders them; the status codes are in
:mod:`myntos.api.errors`. Each is named for the skattemelding even where the
MVA-melding has the same refusal, because the code a client branches on is
the class name (``skattemelding_changed``), and one code meaning two filings
would make an agent read the path to know which. Messages are Norwegian: the
person filing is the one who acts on them.
"""

from myntos.domain.ledger.errors import LedgerError


class SkattemeldingError(LedgerError):
    """Base class for every refusal about a skattemelding."""


class SkattemeldingYearNotSupportedError(SkattemeldingError):
    """Myntos has no schema or code lists for the year, or the year has not ended."""


class SkattemeldingNotReadyError(SkattemeldingError):
    """The books hold something the næringsspesifikasjon cannot be built from.

    The review's ``feil`` findings, which the preview lists. A check is refused
    rather than sent, because Skatteetaten would refuse it too, or worse, take
    figures Myntos knows are wrong.
    """


class SkattemeldingFilingNotFoundError(SkattemeldingError):
    """No such filing, or one belonging to another company."""


class SkattemeldingNotSubmittableError(SkattemeldingError):
    """The filing is not one that may be sent: refused at the check, or already sent."""


class SkattemeldingChangedError(SkattemeldingError):
    """The books moved between the check and the submission.

    The person confirmed the checked næringsspesifikasjon. Sending a freshly
    built one that differs would file figures nobody looked at, so the answer
    is to check again and look.
    """


class SkattemeldingConfirmationRequiredError(SkattemeldingError):
    """The submission was not confirmed against the figure the person saw."""


class SkattemeldingAnswerRequiredError(SkattemeldingError):
    """The check needs an answer only the person can give, and the request has none.

    For a wound-up company: what was paid out to the shareholders
    (``likvidasjonsvederlag``), and, when Enhetsregisteret no longer lists the
    deleted company's roles, whether it had a revisor.
    """


class SkattemeldingNotAvailableError(SkattemeldingError):
    """Skatteetaten has no skattemelding to serve for a forhåndsfastsetting, or not yet.

    Either it is still preparing one (the answer comes a minute or so after
    asking), or it refused to prepare one; the message says which, in
    Skatteetaten's terms translated, and what to do.
    """
