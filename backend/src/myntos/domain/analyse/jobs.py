"""KI reading a company's books after the answer: the ``merking`` run.

docs/analyse.md «Runs: work done after the answer». Modelled on SAF-T's
import (:mod:`myntos.saft.jobs`): a ``ki_run`` row with ``status``, ``step``,
``done`` and ``total`` that a page polls, written by work that opens its own
sessions because it outlives the request that started it.

- **One active run per company and kind**, by the partial unique index
  ``uq_ki_run_active``. Two requests can both look and both find nothing, so
  :func:`start_merking` inserts and, when the index refuses, returns the run
  that won.
- **Two questions to Løynd at a time**, across every company
  (``ki_analysis_concurrency``). Interactive KI is not behind it: a bilag
  someone waits for goes first, and analysis is the thing that waits.
- **Before every batch**, after waiting its turn: the run is not cancelled,
  and the company still has KI on (rule 10). A run that finds KI off stops
  with ``ki_turned_off`` and asks nothing more, even a batch it had ready.
- **Progress is committed per batch**, with the batch's answers in the same
  transaction, so a restart repeats at most one batch and the plan made after
  it skips everything already stored (:mod:`.freshness`).
- **Usage** the way every KI box records it: ``ki_service.resolve`` before,
  ``record_usage`` once at the end (also when the run stops early), and
  ``NO_PLAN``/``SPENT``/``REFUSED`` as notices on the run.

**One process**, as SAF-T's: which runs are being worked on is kept here
(:data:`_executing`), exact with the one uvicorn process this deployment
runs. A restart stops a run in the middle; :func:`resume` runs at startup and
starts it again.

The model comes in through :class:`Ki`, so this package does not import the
Løynd client (``myntos.api.analyse_ki`` builds the real one, a test a fake).
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from fastapi import BackgroundTasks
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.analyse import figures, freshness, items, packet, report, tagging, vocabulary
from myntos.domain.analyse.items import Item, ItemKind
from myntos.domain.analyse.models import (
    ACTIVE_RUN_STATUSES,
    KiReport,
    KiRun,
    KiVoucherAnalysis,
    RunKind,
    RunStatus,
)
from myntos.domain.ki import service as ki_service
from myntos.domain.ki.errors import KiModelUnavailableError, KiTurnedOffError
from myntos.domain.ledger.chart import CLOSING_JOURNAL_ID, OPENING_JOURNAL_ID
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.report import group_of
from myntos.integrations.loynd import Level

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]

#: ``error_code`` of a run that found the company's KI switched off (rule 10).
KI_TURNED_OFF = "ki_turned_off"
#: ``error_code`` of a run Løynd did not answer, or that has no Løynd at all.
#: The vouchers of the batch it stopped at have no row, so the next run asks
#: them.
KI_UNAVAILABLE = "ki_unavailable"
#: ``error_code`` of a run that failed on our side. Logged with its run id
#: only (docs/operator.md).
INTERNAL_ERROR = "internal_error"

#: The step texts the page may show, in bokmål.
STEP_WAITING = "Venter på tur"
STEP_READING = "Leser bilagene"
STEP_CALCULATING = "Regner ut tallene"
STEP_ASKING = "Spør KI-en ({level})"
STEP_CHECKING = "Sjekker svaret"

#: How long after a run Løynd did not answer no new ``merking`` run starts.
#: The reading starts from every visit to the figures, and without this a
#: Løynd that is down would leave a failed run behind each of them.
UNAVAILABLE_PAUSE = timedelta(minutes=10)

#: How old an active run no worker in this process holds must be before
#: :func:`start_merking` starts it again. A run is scheduled when its request
#: answers; one still waiting after this was dropped (the response failed
#: before its background tasks ran, or the process restarted and
#: :func:`resume` has not reached it yet; a run is started once either way).
_STALLED = timedelta(minutes=1)

#: The runs this process is working on now. See the module docstring.
_executing: set[int] = set()

#: The tasks started here rather than by a request's BackgroundTasks, held so
#: they are not collected mid-run.
_tasks: set[asyncio.Task[None]] = set()

#: The limit on questions in flight, one per event loop: a semaphore belongs
#: to the loop it is first used in, and the tests run one loop per test.
_limits: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


# --- The model, injected --------------------------------------------------------------


class Counted(Protocol):
    """Tokens added up across a handle's answers."""

    @property
    def total(self) -> int: ...


class Asker(Protocol):
    """One handle on a model at a level, kept for a run: ``loynd.Model``'s shape.

    Its counters add up across every question the run asks, and the stamp is
    read off it after each answer.
    """

    @property
    def level(self) -> Level: ...
    @property
    def fell_back(self) -> bool: ...
    @property
    def answered_model(self) -> str | None: ...
    @property
    def answered_location(self) -> str | None: ...
    @property
    def usage(self) -> Counted: ...
    @property
    def reported(self) -> Counted: ...

    async def cost_nok(self) -> Decimal: ...

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        history: Sequence[tuple[str, str]] = (),
    ) -> str: ...


@dataclass(frozen=True)
class Ki:
    """What a run needs of Løynd, handed in by whoever starts or resumes it."""

    #: A handle at the level a run was resolved to.
    model: Callable[[Level], Asker]
    #: Which concrete model answers at each level today (``loynd.current_models``).
    current: Callable[[], Awaitable[Mapping[str, freshness.Served]]]
    #: Whether there is a model at all. A deployment without one starts no run.
    configured: Callable[[], bool] = lambda: True
    #: What an ask raises when the model did not answer. Ends the run as
    #: :data:`KI_UNAVAILABLE` rather than as our failure.
    unavailable: tuple[type[BaseException], ...] = ()


# --- Reading runs -----------------------------------------------------------------------


async def get_run(db: AsyncSession, company_id: int, run_id: int) -> KiRun | None:
    """One of this company's runs, or None. Never another company's."""
    run: KiRun | None = await db.scalar(
        select(KiRun).where(KiRun.id == run_id, KiRun.company_id == company_id)
    )
    return run


async def active_run(
    db: AsyncSession, company_id: int, kind: RunKind = RunKind.MERKING
) -> KiRun | None:
    """The company's queued or running run of ``kind``; there is at most one."""
    run: KiRun | None = await db.scalar(
        select(KiRun).where(
            KiRun.company_id == company_id,
            KiRun.kind == kind.value,
            KiRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    )
    return run


def is_active(run: KiRun) -> bool:
    return run.status in ACTIVE_RUN_STATUSES


async def cancel(db: AsyncSession, company_id: int, run_id: int) -> KiRun | None:
    """Stop a run before its next batch. The caller commits.

    What was stored stays: every batch is a voucher's whole answer or nothing.
    A run already finished is returned as it is.
    """
    run = await get_run(db, company_id, run_id)
    if run is not None and is_active(run):
        run.status = RunStatus.CANCELLED.value
        run.step = None
        run.finished_at = utc_now()
        await db.flush()
    return run


# --- Reading reports (WP6) --------------------------------------------------------------


async def get_report(db: AsyncSession, company_id: int, report_id: int) -> KiReport | None:
    """One of this company's reports, or None. Never another company's."""
    row: KiReport | None = await db.scalar(
        select(KiReport).where(KiReport.id == report_id, KiReport.company_id == company_id)
    )
    return row


async def list_reports(
    db: AsyncSession, company_id: int, *, kind: RunKind | None = None, limit: int = 50
) -> list[KiReport]:
    """The company's finished reports, newest first."""
    query = select(KiReport).where(KiReport.company_id == company_id)
    if kind is not None:
        query = query.where(KiReport.kind == kind.value)
    rows = await db.scalars(
        query.order_by(KiReport.created_at.desc(), KiReport.id.desc()).limit(limit)
    )
    return list(rows)


async def find_cached_report(
    db: AsyncSession,
    company_id: int,
    *,
    kind: str,
    input_hash: str,
    min_level: str,
    prompt_version: int = report.PROMPT_VERSION,
    schema_version: int = report.SCHEMA_VERSION,
) -> KiReport | None:
    """Newest report matching ``input_hash`` at ``min_level`` or higher (rule 6)."""
    rows = await db.scalars(
        select(KiReport)
        .where(
            KiReport.company_id == company_id,
            KiReport.kind == kind,
            KiReport.input_hash == input_hash,
            KiReport.prompt_version == prompt_version,
            KiReport.schema_version == schema_version,
        )
        .order_by(KiReport.created_at.desc(), KiReport.id.desc())
    )
    wanted_rank = freshness.rank(min_level)
    for row in rows:
        if freshness.rank(row.level) >= wanted_rank:
            return row
    return None


async def report_for_run(db: AsyncSession, company_id: int, run: KiRun) -> KiReport | None:
    """The report a completed analysis run produced or reused from cache."""
    direct: KiReport | None = await db.scalar(
        select(KiReport).where(KiReport.company_id == company_id, KiReport.run_id == run.id)
    )
    if direct is not None:
        return direct
    if run.status == RunStatus.DONE.value and run.input_hash:
        return await find_cached_report(
            db,
            company_id,
            kind=run.kind,
            input_hash=run.input_hash,
            min_level=Level.FREE.value,
        )
    return None


async def new_vouchers_since(
    db: AsyncSession, company_id: int, *, fra: date, til: date, since: datetime
) -> int:
    """Posted income/cost vouchers in ``fra..til`` (or its comparison) booked after ``since``."""
    before_fra, before_til = items.comparison_of(fra, til)
    accounts = await db.scalars(
        select(Account).where(
            Account.company_id == company_id,
            Account.account_type.in_((AccountType.INNTEKT, AccountType.KOSTNAD)),
        )
    )
    wanted = {
        a.id
        for a in accounts
        if group_of(a.account_number, a.account_type).key not in {"skattekostnad", "disponering"}
    }
    if not wanted:
        return 0
    rows = await db.execute(
        select(Transaction.id, Transaction.posted_at)
        .select_from(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company_id,
            Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
            Transaction.posted_at.is_not(None),
            TransactionLine.account_id.in_(wanted),
            (
                (Transaction.transaction_date >= fra) & (Transaction.transaction_date <= til)
                | (
                    (Transaction.transaction_date >= before_fra)
                    & (Transaction.transaction_date <= before_til)
                )
            ),
        )
        .distinct()
    )
    cutoff = as_utc(since)
    return sum(1 for _, posted_at in rows if posted_at is not None and as_utc(posted_at) > cutoff)


# --- What a run reads -------------------------------------------------------------------


@dataclass
class Plan:
    """The vouchers a run reads, in the order it reads them."""

    #: To ask about, the period newest first and then its comparison.
    ask: list[Item] = field(default_factory=list)
    #: Depreciation and reversals whose stored analysis is stale now.
    known: list[Item] = field(default_factory=list)
    #: Every depreciation and reversal in scope, stale or not: a reversal
    #: whose original this run reads again is stale after it.
    deterministic: list[Item] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.ask) + len(self.known)


def _known_stale(
    item: Item,
    rows: Mapping[int, KiVoucherAnalysis],
    *,
    wanted: str,
    now: datetime | None,
) -> bool:
    if item.kind is ItemKind.REVERSAL and item.reverses_pk is not None:
        input_hash = items.reversal_hash(item, rows.get(item.reverses_pk))
    else:
        input_hash = items.input_hash(item)
    result = freshness.verdict(
        rows.get(item.transaction_pk), input_hash=input_hash, wanted=wanted, current={}, now=now
    )
    return freshness.acts_on(result, person=False)


async def plan(
    db: AsyncSession,
    company_id: int,
    fra: date,
    til: date,
    *,
    wanted: str,
    person: bool,
    now: datetime | None = None,
) -> Plan:
    """What a run over ``fra``..``til`` at ``wanted`` would read now.

    The period newest first, then its comparison (a first visit to this year
    does not wait for last year), then the originals of reversals in them
    that lie outside both, since a reversal mirrors what its original says.

    ``wanted`` is the level the run is answered at (after
    ``ki_service.resolve``), which is also what the plan allows: a row from a
    dearer level is fresh, never read again lower. ``person`` is a run a
    person started at a paid level, which also reads the rows answered lower
    than it (``upgrade``, ``fell_back``). No run here acts on ``prompt`` or
    ``model``, so which model answers today does not change the plan.
    """
    found: dict[int, Item] = {}
    compared = items.comparison_of(fra, til)
    for item in [
        *await items.in_period(db, company_id, fra, til),
        *await items.in_period(db, company_id, *compared),
    ]:
        found.setdefault(item.transaction_pk, item)
    outside = {
        item.reverses_pk
        for item in found.values()
        if item.reverses_pk is not None and item.reverses_pk not in found
    }
    for item in await items.by_pk(db, company_id, outside):
        found.setdefault(item.transaction_pk, item)
    rows = await tagging.analyses(db, company_id, found)
    result = Plan()
    for item in found.values():
        if item.kind is ItemKind.KI:
            verdict = freshness.verdict(
                rows.get(item.transaction_pk),
                input_hash=items.input_hash(item),
                wanted=wanted,
                current={},
                allowed=wanted,
                now=now,
            )
            if freshness.acts_on(verdict, person=person):
                result.ask.append(item)
            continue
        result.deterministic.append(item)
        if _known_stale(item, rows, wanted=wanted, now=now):
            result.known.append(item)
    return result


# --- Starting a run ---------------------------------------------------------------------


def _schedule(sessions: Sessions, ki: Ki, run_id: int, background: BackgroundTasks | None) -> None:
    # The coroutine function itself to BackgroundTasks: handed a lambda, it
    # would call it in a thread and drop the coroutine unawaited.
    if background is not None:
        background.add_task(execute, sessions, ki, run_id)
        return
    task = asyncio.get_running_loop().create_task(execute(sessions, ki, run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _paused(db: AsyncSession, company_id: int, now: datetime) -> bool:
    """Whether the company's last reading found Løynd not answering, a moment ago."""
    last: KiRun | None = await db.scalar(
        select(KiRun)
        .where(KiRun.company_id == company_id, KiRun.kind == RunKind.MERKING.value)
        .order_by(KiRun.id.desc())
        .limit(1)
    )
    return (
        last is not None
        and last.error_code == KI_UNAVAILABLE
        and last.finished_at is not None
        and now - as_utc(last.finished_at) < UNAVAILABLE_PAUSE
    )


async def start_merking(
    db: AsyncSession,
    sessions: Sessions,
    ki: Ki,
    company: Company,
    *,
    fra: date,
    til: date,
    level: Level = Level.FREE,
    requested_by_user_id: int | None = None,
    via_agent_key_id: int | None = None,
    background: BackgroundTasks | None = None,
    now: datetime | None = None,
) -> KiRun | None:
    """Start reading the period and its comparison, or return the reading under way.

    Returns the company's active ``merking`` run when there is one (the
    period it reads may be another: a second visit waits for the first
    reading rather than starting a second). Otherwise starts one when
    something needs reading and returns it, or returns None: KI is off, there
    is no model, nothing needs reading (every voucher is stored, or failed
    inside its back-off), or Løynd did not answer the last run a moment ago.

    **Commits** ``db``, so the run is there for the work's own session and
    the unique index has settled who started it. The work goes on
    ``background`` when a route hands its ``BackgroundTasks`` in (it starts
    when the response is sent), else on a task of its own.
    """
    if not company.ki_enabled or not ki.configured():
        return None
    moment = now or utc_now()
    active = await active_run(db, company.id)
    if active is not None:
        if active.id not in _executing and moment - as_utc(active.created_at) > _STALLED:
            _schedule(sessions, ki, active.id, background)
        return active
    if await _paused(db, company.id, moment):
        return None
    resolution = await ki_service.resolve(db, company.id, level, now=moment)
    found = await plan(
        db,
        company.id,
        fra,
        til,
        wanted=resolution.level.value,
        person=level.is_paid,
        now=moment,
    )
    if not found.size:
        return None
    run = KiRun(
        company_id=company.id,
        kind=RunKind.MERKING.value,
        status=RunStatus.QUEUED.value,
        requested_level=level.value,
        period_from=fra,
        period_to=til,
        step=STEP_WAITING,
        done=0,
        total=found.size,
        notices=[],
        requested_by_user_id=requested_by_user_id,
        via_agent_key_id=via_agent_key_id,
        created_at=moment,
    )
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
    except IntegrityError:
        # Another request started one between the look and the insert.
        return await active_run(db, company.id)
    await db.commit()
    _schedule(sessions, ki, run.id, background)
    return run


async def start_dyp(
    db: AsyncSession,
    sessions: Sessions,
    ki: Ki,
    company: Company,
    *,
    fra: date,
    til: date,
    level: Level = Level.FREE,
    requested_by_user_id: int | None = None,
    via_agent_key_id: int | None = None,
    background: BackgroundTasks | None = None,
    now: datetime | None = None,
) -> tuple[KiRun | None, KiReport | None]:
    """Start a «Dyp KI-analyse» run, return an active one, or return a cached report.

    Returns ``(None, cached_report)`` (0 tokens, no run created) when every
    voucher in scope is already read and a fresh report exists for the exact
    packet hash at the resolved level or higher (rule 6). Otherwise starts a
    ``dyp`` run (or returns the company's active ``dyp`` run) and returns
    ``(run, None)``.
    """
    if not company.ki_enabled:
        raise KiTurnedOffError("KI er slått av for dette selskapet")
    chosen = figures.period(fra, til)
    moment = now or utc_now()
    active = await active_run(db, company.id, kind=RunKind.DYP)
    if active is not None:
        if active.id not in _executing and moment - as_utc(active.created_at) > _STALLED:
            _schedule(sessions, ki, active.id, background)
        return active, None

    resolution = await ki_service.resolve(db, company.id, level, now=moment)
    found = await plan(
        db,
        company.id,
        chosen.fra,
        chosen.til,
        wanted=Level.FREE.value,
        person=False,
        now=moment,
    )
    if found.size == 0:
        pkt = await packet.build(
            db,
            company=company,
            chosen=chosen,
            prompt_version=report.PROMPT_VERSION,
            schema_version=report.SCHEMA_VERSION,
        )
        cached = await find_cached_report(
            db,
            company.id,
            kind=RunKind.DYP.value,
            input_hash=pkt.input_hash,
            min_level=resolution.level.value,
        )
        if cached is not None:
            return None, cached

    if not ki.configured():
        raise KiModelUnavailableError("KI er ikke tilgjengelig akkurat nå")

    run = KiRun(
        company_id=company.id,
        kind=RunKind.DYP.value,
        status=RunStatus.QUEUED.value,
        requested_level=level.value,
        period_from=chosen.fra,
        period_to=chosen.til,
        step=STEP_WAITING,
        done=0,
        total=found.size,
        notices=[resolution.notice] if resolution.notice else [],
        requested_by_user_id=requested_by_user_id,
        via_agent_key_id=via_agent_key_id,
        created_at=moment,
    )
    try:
        async with db.begin_nested():
            db.add(run)
            await db.flush()
    except IntegrityError:
        return await active_run(db, company.id, kind=RunKind.DYP), None
    await db.commit()
    _schedule(sessions, ki, run.id, background)
    return run, None


# --- The work ---------------------------------------------------------------------------


def _limit() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    found = _limits.get(loop)
    if found is None:
        found = asyncio.Semaphore(max(1, get_settings().ki_analysis_concurrency))
        _limits[loop] = found
    return found


class _Stop(Exception):  # noqa: N818 (control flow, not an error)
    """The run ends here, with this status and code."""

    def __init__(self, status: RunStatus, error_code: str | None) -> None:
        super().__init__(error_code or status.value)
        self.status = status
        self.error_code = error_code


@dataclass
class _Work:
    """A run in this process: what it was resolved to and what it has spent."""

    run_id: int
    company_id: int
    requested_level: str
    notices: list[str] = field(default_factory=list)
    period_id: int | None = None
    week_start: date | None = None
    asker: Asker | None = None
    #: Separate free-level handle used for step 1 tagging inside a ``dyp`` run,
    #: so paid plan tokens are never spent reading individual vouchers.
    tagging_asker: Asker | None = None
    #: The run's counters when this process took it up: a resumed run adds
    #: what it spends now to what the stopped process wrote.
    charged_before: int = 0
    reported_before: int = 0

    def tokens(self) -> dict[str, int]:
        charged = (self.asker.usage.total if self.asker is not None else 0) + (
            self.tagging_asker.usage.total if self.tagging_asker is not None else 0
        )
        reported = (self.asker.reported.total if self.asker is not None else 0) + (
            self.tagging_asker.reported.total if self.tagging_asker is not None else 0
        )
        return {
            "tokens_charged": self.charged_before + charged,
            "tokens_total": self.reported_before + reported,
        }


async def _check(sessions: Sessions, work: _Work) -> None:
    """Raise :class:`_Stop` when the run may not ask its next batch."""
    async with sessions() as db:
        found = (
            await db.execute(
                select(KiRun.status, Company.ki_enabled)
                .join(Company, Company.id == KiRun.company_id)
                .where(KiRun.id == work.run_id)
            )
        ).first()
    if found is None:  # the company and its runs were deleted
        raise _Stop(RunStatus.CANCELLED, None)
    status, enabled = found
    if status not in ACTIVE_RUN_STATUSES:
        raise _Stop(RunStatus(status), None)
    if not enabled:
        raise _Stop(RunStatus.CANCELLED, KI_TURNED_OFF)


async def _store(
    sessions: Sessions,
    work: _Work,
    outcomes: Sequence[tagging.Outcome],
    vocab: tagging.Vocabulary,
) -> None:
    """Store a batch and its progress in one transaction, once more on a lost race.

    The race is a tag another run stored between this run's look and its
    insert (:func:`tagging.store`); the second time finds it.
    """
    for attempt in (1, 2):
        async with sessions() as db:
            try:
                new = await tagging.store(
                    db,
                    company_id=work.company_id,
                    run_id=work.run_id,
                    outcomes=outcomes,
                    vocab=vocab,
                )
                await db.execute(
                    update(KiRun)
                    .where(KiRun.id == work.run_id)
                    .values(done=KiRun.done + len(outcomes), **work.tokens())
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if attempt == 2:
                    raise
                continue
        vocab.stored(new)
        return


async def _ensure_seeds(sessions: Sessions, company: Company) -> None:
    """The seeds, committed; once more when a run for another kind inserted them first."""
    for attempt in (1, 2):
        async with sessions() as db:
            try:
                await vocabulary.ensure_seeds(db, company)
                await db.commit()
                return
            except IntegrityError:
                await db.rollback()
                if attempt == 2:
                    raise


def _stamp(work: _Work, asker: Asker, current: Mapping[str, freshness.Served]) -> tagging.Stamp:
    """Who answered the question just asked, read off the handle.

    The model as Løynd's header named it, else what ``/v1/models`` says
    answers at that level; the checkpoint only where that is the same model,
    since it is the free model's and dates nothing else.
    """
    level = asker.level.value
    served = current.get(level)
    model = asker.answered_model or (served.model if served is not None else None)
    revision = served.revision if served is not None and served.model == model else None
    return tagging.Stamp(
        requested_level=work.requested_level,
        level=level,
        model=model,
        model_revision=revision,
        location=asker.answered_location,
    )


async def _tag_period(
    sessions: Sessions,
    ki: Ki,
    work: _Work,
    company: Company,
    fra: date,
    til: date,
    *,
    level: Level,
    person: bool,
    as_tagging_step: bool = False,
) -> None:
    """Read every stale/missing voucher in ``fra..til`` (and its comparison) and store tags."""
    await _ensure_seeds(sessions, company)
    async with sessions() as db:
        vocab = tagging.Vocabulary(await vocabulary.load(db, company.id))
        found = await plan(
            db,
            company.id,
            fra,
            til,
            wanted=level.value,
            person=person,
        )
        await db.execute(
            update(KiRun).where(KiRun.id == work.run_id).values(total=KiRun.done + found.size)
        )
        await db.commit()
    settings = get_settings()
    budget = settings.ki_analysis_answer_tokens
    asker = ki.model(level)
    if as_tagging_step:
        work.tagging_asker = asker
    else:
        work.asker = asker
    current: Mapping[str, freshness.Served] = await ki.current() if found.ask else {}
    head = tagging.prefix(company, vocabulary.ordered(vocab.tags, company))
    for batch in tagging.batches(found.ask, limit=settings.ki_analysis_batch_size, budget=budget):
        stopped: Exception | None = None
        async with _limit():
            await _check(sessions, work)
            try:
                outcomes, tally = await tagging.read_batch(
                    asker.complete,
                    lambda: _stamp(work, asker, current),
                    head,
                    batch,
                    vocab,
                    budget=budget,
                )
            except tagging.Interrupted as interrupted:
                outcomes, tally = interrupted.outcomes, interrupted.tally
                cause = interrupted.__cause__
                stopped = cause if isinstance(cause, Exception) else interrupted
            except ki.unavailable as error:
                logger.error(
                    "KI run %s failed (%s): Løynd did not answer (%s)",
                    work.run_id,
                    KI_UNAVAILABLE,
                    type(error).__name__,
                    exc_info=error,
                )
                raise _Stop(RunStatus.FAILED, KI_UNAVAILABLE) from None
        logger.info(
            "KI run %s: %s asked, %s objects, %s valid, %s asked alone, %s failed",
            work.run_id,
            tally.asked,
            tally.objects,
            tally.valid,
            tally.asked_alone,
            tally.failed,
        )
        before = len(vocab.tags)
        await _store(sessions, work, outcomes, vocab)
        if stopped is not None:
            if isinstance(stopped, ki.unavailable):
                logger.error(
                    "KI run %s failed (%s): Løynd did not answer (%s)",
                    work.run_id,
                    KI_UNAVAILABLE,
                    type(stopped).__name__,
                    exc_info=stopped,
                )
                raise _Stop(RunStatus.FAILED, KI_UNAVAILABLE) from None
            raise stopped
        if len(vocab.tags) != before:
            head = tagging.prefix(company, vocabulary.ordered(vocab.tags, company))
    await _check(sessions, work)
    async with sessions() as db:
        rows = await tagging.analyses(db, company.id, _with_originals(found.deterministic))
        stale = [
            item
            for item in found.deterministic
            if _known_stale(item, rows, wanted=level.value, now=None)
        ]
        written = await tagging.write_known(
            db,
            company_id=company.id,
            run_id=work.run_id,
            known=stale,
            tags=vocab.tags,
            level=level.value,
        )
        await db.execute(
            update(KiRun).where(KiRun.id == work.run_id).values(done=KiRun.done + written)
        )
        await db.commit()


async def _read(sessions: Sessions, ki: Ki, work: _Work) -> None:
    async with sessions() as db:
        run = await db.get(KiRun, work.run_id)
        if run is None or not is_active(run):
            raise _Stop(RunStatus(run.status) if run is not None else RunStatus.CANCELLED, None)
        company = await db.get(Company, run.company_id)
        if company is None:
            raise _Stop(RunStatus.CANCELLED, None)
        if not company.ki_enabled:
            raise _Stop(RunStatus.CANCELLED, KI_TURNED_OFF)
        if not ki.configured():
            logger.error("KI run %s failed (%s): no model configured", work.run_id, KI_UNAVAILABLE)
            raise _Stop(RunStatus.FAILED, KI_UNAVAILABLE)
        wanted = Level(run.requested_level)
        resolution = await ki_service.resolve(db, company.id, wanted)
        work.period_id = resolution.period_id
        work.week_start = resolution.week_start
        work.notices = [resolution.notice] if resolution.notice else []
        work.charged_before = run.tokens_charged
        work.reported_before = run.tokens_total
        fra, til = run.period_from, run.period_to
        if fra is None or til is None:  # a merking run always has its period
            logger.error("KI run %s failed (%s): missing period", work.run_id, INTERNAL_ERROR)
            raise _Stop(RunStatus.FAILED, INTERNAL_ERROR)
        run.status = RunStatus.RUNNING.value
        run.step = STEP_READING
        run.started_at = run.started_at or utc_now()
        run.ki_period_id = resolution.period_id
        run.notices = list(work.notices)
        await db.commit()
    await _tag_period(
        sessions,
        ki,
        work,
        company,
        fra,
        til,
        level=resolution.level,
        person=wanted.is_paid,
    )


async def _run_dyp(sessions: Sessions, ki: Ki, work: _Work) -> None:
    """Four steps of «Dyp KI-analyse»: tag at free, build packet, ask model, check and store."""
    async with sessions() as db:
        run = await db.get(KiRun, work.run_id)
        if run is None or not is_active(run):
            raise _Stop(RunStatus(run.status) if run is not None else RunStatus.CANCELLED, None)
        company = await db.get(Company, run.company_id)
        if company is None:
            raise _Stop(RunStatus.CANCELLED, None)
        if not company.ki_enabled:
            raise _Stop(RunStatus.CANCELLED, KI_TURNED_OFF)
        if not ki.configured():
            logger.error("KI run %s failed (%s): no model configured", work.run_id, KI_UNAVAILABLE)
            raise _Stop(RunStatus.FAILED, KI_UNAVAILABLE)
        wanted = Level(run.requested_level)
        resolution = await ki_service.resolve(db, company.id, wanted)
        work.period_id = resolution.period_id
        work.week_start = resolution.week_start
        work.notices = [resolution.notice] if resolution.notice else []
        work.charged_before = run.tokens_charged
        work.reported_before = run.tokens_total
        fra, til = run.period_from, run.period_to
        if fra is None or til is None:
            logger.error("KI run %s failed (%s): missing period", work.run_id, INTERNAL_ERROR)
            raise _Stop(RunStatus.FAILED, INTERNAL_ERROR)
        run.status = RunStatus.RUNNING.value
        run.step = STEP_READING
        run.started_at = run.started_at or utc_now()
        run.ki_period_id = resolution.period_id
        run.notices = list(work.notices)
        await db.commit()

    # Step 1: ensure vouchers in period and comparison are tagged at free (never spends plan).
    await _tag_period(
        sessions,
        ki,
        work,
        company,
        fra,
        til,
        level=Level.FREE,
        person=False,
        as_tagging_step=True,
    )

    # Step 2: build deterministic packet and check report cache.
    await _check(sessions, work)
    async with sessions() as db:
        await db.execute(update(KiRun).where(KiRun.id == work.run_id).values(step=STEP_CALCULATING))
        await db.commit()
    async with sessions() as db:
        fresh_company = await db.get(Company, work.company_id)
        if fresh_company is None:
            raise _Stop(RunStatus.CANCELLED, None)
        if not fresh_company.ki_enabled:
            raise _Stop(RunStatus.CANCELLED, KI_TURNED_OFF)
        pkt = await packet.build(
            db,
            company=fresh_company,
            chosen=figures.period(fra, til),
            prompt_version=report.PROMPT_VERSION,
            schema_version=report.SCHEMA_VERSION,
        )
        await db.execute(
            update(KiRun).where(KiRun.id == work.run_id).values(input_hash=pkt.input_hash)
        )
        cached = await find_cached_report(
            db,
            work.company_id,
            kind=RunKind.DYP.value,
            input_hash=pkt.input_hash,
            min_level=resolution.level.value,
        )
        await db.commit()
    if cached is not None:
        logger.info("KI run %s: reused cached dyp report %s", work.run_id, cached.id)
        return

    # Step 3: ask the model at the resolved level.
    asking_step = STEP_ASKING.format(level=ki_service.level_name(resolution.level))
    async with sessions() as db:
        await db.execute(update(KiRun).where(KiRun.id == work.run_id).values(step=asking_step))
        await db.commit()

    asker = ki.model(resolution.level)
    work.asker = asker
    async with _limit():
        await _check(sessions, work)
        try:
            body = await report.generate(asker.complete, pkt)
        except ki.unavailable as error:
            logger.error(
                "KI run %s failed (%s): Løynd did not answer (%s)",
                work.run_id,
                KI_UNAVAILABLE,
                type(error).__name__,
                exc_info=error,
            )
            raise _Stop(RunStatus.FAILED, KI_UNAVAILABLE) from None
        except report.InvalidReportError as error:
            logger.error(
                "KI run %s failed (%s): invalid report JSON after retry",
                work.run_id,
                INTERNAL_ERROR,
                exc_info=error,
            )
            raise _Stop(RunStatus.FAILED, INTERNAL_ERROR) from None

    # Step 4: final check before storing stamped report.
    async with sessions() as db:
        await db.execute(update(KiRun).where(KiRun.id == work.run_id).values(step=STEP_CHECKING))
        await db.commit()
    await _check(sessions, work)
    current = await ki.current()
    stamp = _stamp(work, asker, current)
    async with sessions() as db:
        row = KiReport(
            company_id=work.company_id,
            run_id=work.run_id,
            kind=RunKind.DYP.value,
            period_from=fra,
            period_to=til,
            input_hash=pkt.input_hash,
            prompt_version=report.PROMPT_VERSION,
            schema_version=report.SCHEMA_VERSION,
            requested_level=work.requested_level,
            level=stamp.level,
            model=stamp.model,
            model_revision=stamp.model_revision,
            location=stamp.location,
            body=body.model_dump(),
        )
        db.add(row)
        await db.flush()
        report_id = row.id
        await db.commit()
    logger.info(
        "KI run %s: stored dyp report %s (%s paragraphs dropped)",
        work.run_id,
        report_id,
        body.dropped_paragraphs,
    )


def _with_originals(known: Sequence[Item]) -> list[int]:
    return [
        *(item.transaction_pk for item in known),
        *(item.reverses_pk for item in known if item.reverses_pk is not None),
    ]


async def _finish(
    sessions: Sessions, work: _Work, status: RunStatus, error_code: str | None
) -> None:
    """Close the run and count what it spent, once, however it ended."""
    notices = list(work.notices)
    if work.asker is not None and work.asker.fell_back:
        notices.append(ki_service.REFUSED)
    async with sessions() as db:
        run = await db.get(KiRun, work.run_id)
        if run is None:
            return
        if is_active(run):
            run.status = status.value
            run.error_code = error_code
        if run.status == RunStatus.DONE.value:
            run.total = max(run.total, run.done)
        run.step = None
        run.finished_at = run.finished_at or utc_now()
        run.notices = notices
        for name, value in work.tokens().items():
            setattr(run, name, value)
        if work.asker is not None:
            cost = await work.asker.cost_nok()
            tokens = work.asker.usage.total
            if cost > 0 or tokens > 0:
                await ki_service.record_usage(
                    db,
                    work.period_id,
                    cost,
                    tokens=tokens,
                    company_id=work.company_id,
                    week_start=work.week_start,
                )
        await db.commit()


async def execute(sessions: Sessions, ki: Ki, run_id: int) -> None:
    """Do a run's work, from wherever it stopped (started by ``start_merking``/``start_dyp``)."""
    if run_id in _executing:
        return
    _executing.add(run_id)
    try:
        async with sessions() as db:
            found = (
                await db.execute(
                    select(KiRun.company_id, KiRun.requested_level, KiRun.kind).where(
                        KiRun.id == run_id
                    )
                )
            ).first()
        if found is None:
            return
        work = _Work(run_id=run_id, company_id=found[0], requested_level=found[1])
        kind = found[2]
        try:
            if kind == RunKind.DYP.value:
                await _run_dyp(sessions, ki, work)
            else:
                await _read(sessions, ki, work)
        except _Stop as stop:
            await _finish(sessions, work, stop.status, stop.error_code)
            return
        except Exception:
            logger.exception("KI run %s failed", run_id)
            await _finish(sessions, work, RunStatus.FAILED, INTERNAL_ERROR)
            return
        await _finish(sessions, work, RunStatus.DONE, None)
    finally:
        _executing.discard(run_id)


# --- After a restart --------------------------------------------------------------------


async def resume(sessions: Sessions, ki: Ki) -> None:
    """Start again the ``merking`` and ``dyp`` runs a restart stopped. Called once, at startup."""
    async with sessions() as db:
        stopped = list(
            (
                await db.scalars(
                    select(KiRun.id)
                    .where(
                        KiRun.kind.in_((RunKind.MERKING.value, RunKind.DYP.value)),
                        KiRun.status.in_(ACTIVE_RUN_STATUSES),
                    )
                    .order_by(KiRun.id)
                )
            ).all()
        )
    await asyncio.gather(
        *(execute(sessions, ki, run_id) for run_id in stopped if run_id not in _executing)
    )


#: How long after startup :func:`resume_in_background` looks for stopped runs.
#: A stopped reading loses nothing by waiting a few seconds, and by then the
#: rest of startup (SAF-T's resume, above all) has opened the pool's first
#: connection. Two tasks opening it at once leave SQLAlchemy's first-connect
#: lock bound to the app's event loop; a test that then drives the same
#: engine from a loop of its own failed on it (test_saft_innlesing,
#: 2026-11-27).
RESUME_DELAY = 5.0


def resume_in_background(factory: Callable[[], Sessions], ki: Callable[[], Ki]) -> None:
    """Schedule :func:`resume`, a moment after startup and without holding it up."""

    async def run() -> None:
        try:
            await asyncio.sleep(RESUME_DELAY)
            await resume(factory(), ki())
        except Exception:
            logger.exception("Could not resume KI runs after a restart")

    task = asyncio.get_running_loop().create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
