"""What a review step shows before anything is posted.

The same two severities the SAF-T check uses, for the same reason: a **feil**
stops the button, a **merknad** is something a person should read and may well
be right about. Kept as its own small type rather than borrowed from
:mod:`myntos.saft.check`, because the domain must not import from ``saft``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Severity(enum.StrEnum):
    FEIL = "feil"
    MERKNAD = "merknad"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    # Stable and machine-readable, for the frontend and the tests; the message
    # is for people and may be reworded.
    code: str
    message: str


def feil(code: str, message: str) -> Finding:
    return Finding(Severity.FEIL, code, message)


def merknad(code: str, message: str) -> Finding:
    return Finding(Severity.MERKNAD, code, message)


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity is Severity.FEIL]
