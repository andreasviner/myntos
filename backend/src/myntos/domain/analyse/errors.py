"""Refusals from the figures half of Analyse.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, because the
figures are the ledger read another way: the handler already registered for
the ledger renders them, and ``api/errors.py`` gives each its status, so a
new route here needs no handler of its own.
"""

from __future__ import annotations

from myntos.domain.ledger.errors import LedgerError


class AnalyseError(LedgerError):
    """Base for every refusal of a figure or a chart request."""


class UnknownDatasetError(AnalyseError):
    """No dataset by that name in the registry.

    A 404 like a record that is not there: the name is in the path, and an
    agent that guessed one should be told it does not exist, not that its
    request was malformed.
    """


class InvalidAnalysisPeriodError(AnalyseError):
    """``fra`` after ``til``, or a period longer than a chart can show."""


class ChartRequestError(AnalyseError):
    """A dataset asked for as a view it cannot be drawn as, or too many slices for it.

    Refused rather than quietly redrawn, because the caller (a person's
    choice on the page, or a model choosing a chart) should learn which views
    a dataset has from ``GET …/analyse/datasett`` and pick one of them.
    """


class AnalysisRunNotFoundError(AnalyseError):
    """No KI run by that id in this company (WP5b).

    A 404 whether it never existed or is another company's, so a run's id
    tells nobody outside the company that it is there
    (docs/company-isolation.md).
    """


class AnalysisReportNotFoundError(AnalyseError):
    """No KI report by that id in this company (WP6).

    A 404 whether it never existed or is another company's (docs/company-isolation.md).
    """
