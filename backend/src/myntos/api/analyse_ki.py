"""The model a KI reading run asks: Løynd, for the domain that may not import it.

:mod:`myntos.domain.analyse.jobs` takes its model as a :class:`~myntos.domain.analyse.jobs.Ki`
so that the domain never imports the client (docs/analyse.md, WP5). A
dependency, so a route and ``main.lifespan`` (which resumes the runs a
restart stopped) get the same one, and a test overrides it with a fake Løynd
the way it overrides :func:`~myntos.api.v1.saft_innlesing.get_import_sessions`.
"""

from __future__ import annotations

from myntos.core.config import get_settings
from myntos.domain.analyse.jobs import Ki
from myntos.integrations import loynd


def _model(level: loynd.Level) -> loynd.Model:
    # A batch's answer is far longer than a bilag's few fields, and the
    # level's own read timeout is sized for those (core/config.py).
    return loynd.Model(level, read_timeout=get_settings().ki_analysis_read_timeout)


def get_analysis_ki() -> Ki:
    """Løynd as a reading run asks it."""
    return Ki(
        model=_model,
        current=loynd.current_models,
        configured=loynd.is_configured,
        unavailable=(loynd.LoyndError,),
    )
