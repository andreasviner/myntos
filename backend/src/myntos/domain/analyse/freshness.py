"""Whether a voucher's stored analysis still stands, as one pure function.

docs/analyse.md «The stamp, and when to analyse again». Rule 6: stamp
everything, and never pay twice. A voucher already read from the same input,
at the same or a higher level and the current prompt version, is answered
from the database for no tokens, and a cheaper level never overwrites a
dearer one's answer.

:func:`verdict` says *whether* and *at which level*; the caller decides which
reasons its run acts on (:func:`acts_on`), because what a reason costs decides
when it is worth acting on:

- ``missing``, ``changed``, ``failed``: in any run.
- ``upgrade``, ``fell_back``: only in a run a person started at that level.
  Their tokens are not spent on old vouchers because a tier got better.
- ``prompt``, ``model``: reported at the row's own level, never higher, and
  acted on only by the slow top-up at ``free`` (WP8). At a paid level, never
  by themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from myntos.core.clock import as_utc, utc_now
from myntos.domain.analyse.models import AnalysisSource, AnalysisStatus

#: Bumped when the prompt or the rules for reading the answer change
#: meaningfully (domain/analyse/tagging.py). Rows from an older version are
#: ``prompt``-stale.
PROMPT_VERSION = 1

#: The levels, cheapest first. Keyed by the level's value, which is what the
#: stamp stores; :class:`myntos.integrations.loynd.Level` is a ``StrEnum`` and
#: looks itself up here as well.
LEVEL_RANK: Mapping[str, int] = {"free": 0, "medium": 1, "thinking": 2}

#: How long a voucher that failed is left alone before it is asked again: an
#: hour, a day, then a week. A voucher the model cannot answer today will most
#: likely not be answered in five minutes either, and every attempt costs a
#: request; a week is short enough that a new model gets its turn.
BACKOFF = (timedelta(hours=1), timedelta(days=1), timedelta(weeks=1))

Reason = Literal["missing", "changed", "failed", "upgrade", "fell_back", "prompt", "model"]

#: The reasons any run acts on, whoever started it.
ALWAYS: frozenset[Reason] = frozenset({"missing", "changed", "failed"})
#: The reasons only a run a person started at that level acts on.
PERSON: frozenset[Reason] = frozenset({"upgrade", "fell_back"})


class Stamped(Protocol):
    """What :func:`verdict` reads of a ``ki_voucher_analysis`` row."""

    @property
    def status(self) -> str: ...
    @property
    def source(self) -> str: ...
    @property
    def input_hash(self) -> str: ...
    @property
    def prompt_version(self) -> int: ...
    @property
    def requested_level(self) -> str: ...
    @property
    def level(self) -> str: ...
    @property
    def model(self) -> str | None: ...
    @property
    def model_revision(self) -> str | None: ...
    @property
    def analysed_at(self) -> datetime: ...
    @property
    def failures(self) -> int: ...


class Served(Protocol):
    """What answers at a level today: Løynd's ``/v1/models`` and ``/healthz``."""

    @property
    def model(self) -> str: ...
    @property
    def revision(self) -> str | None: ...


@dataclass(frozen=True)
class Fresh:
    """Reuse what is stored: no request, no tokens."""


@dataclass(frozen=True)
class Stale:
    """Ask again, for ``reason``, at ``level``."""

    reason: Reason
    level: str


def rank(level: str) -> int:
    return LEVEL_RANK[str(level)]


def backoff(failures: int) -> timedelta:
    """How long after its last attempt a failed voucher is left alone."""
    return BACKOFF[max(0, min(failures, len(BACKOFF)) - 1)]


def verdict(
    row: Stamped | None,
    *,
    input_hash: str,
    wanted: str,
    current: Mapping[str, Served],
    allowed: str | None = None,
    now: datetime | None = None,
) -> Fresh | Stale:
    """Whether ``row`` still answers for this input, and if not, why and at which level.

    In this order; the first that applies wins:

    ========== ======================================================= ==============
    missing    no row                                                  ``wanted``
    changed    the input hash differs                                  ``wanted``
    failed     it failed, and the back-off has passed                  ``wanted``
    upgrade    ``wanted`` ranks above the level that answered          ``wanted``
    fell_back  it answered below what was asked, and ``allowed``       requested
               (what the plan allows now) reaches what was asked
    prompt     an older prompt version                                 row's level
    model      another model or revision answers at its level today    row's level
    ========== ======================================================= ==============

    A failed row inside its back-off is :class:`Fresh`: there is nothing to
    do for it now. A row the software wrote (``source = 'regnskap'``) knows no
    level, prompt or model to be stale against, so only the first three apply
    to it. ``current`` is keyed by level; a level missing from it (Løynd did
    not say) is never ``model``-stale, since not knowing is no reason to pay.

    **Never downgrade**: nothing here asks below ``row.level`` unless the
    input changed, and then the old answer is about something else.
    """
    if row is None:
        return Stale("missing", str(wanted))
    if row.input_hash != input_hash:
        return Stale("changed", str(wanted))
    if row.status == AnalysisStatus.FAILED:
        moment = now or utc_now()
        if moment - as_utc(row.analysed_at) >= backoff(row.failures):
            return Stale("failed", str(wanted))
        return Fresh()
    if row.source == AnalysisSource.REGNSKAP:
        return Fresh()
    if rank(wanted) > rank(row.level):
        return Stale("upgrade", str(wanted))
    if (
        rank(row.level) < rank(row.requested_level)
        and allowed is not None
        and rank(allowed) >= rank(row.requested_level)
    ):
        return Stale("fell_back", row.requested_level)
    if row.prompt_version < PROMPT_VERSION:
        return Stale("prompt", row.level)
    served = current.get(row.level)
    if served is not None and (row.model, row.model_revision) != (
        served.model,
        served.revision,
    ):
        return Stale("model", row.level)
    return Fresh()


def acts_on(result: Fresh | Stale, *, person: bool) -> bool:
    """Whether a reading run acts on this verdict.

    ``person`` is a run a person started at its level by pressing something;
    the background reading at ``free`` that a visit starts is not one.
    """
    if not isinstance(result, Stale):
        return False
    return result.reason in ALWAYS or (person and result.reason in PERSON)
