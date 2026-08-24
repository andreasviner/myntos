"""Skatteetaten API clients: skattemelding, næringsspesifikasjon, MVA-melding.

Split by auth plane, because Skatteetaten splits them and the split has legal
force: :mod:`.client` reads and validates the skattemelding over a Maskinporten
token and submits it with a person's, while everything in :mod:`.mva` needs a
person from the first call onwards. Neither module builds the XML — that is the
domain's work, in the MVA and skattemelding milestones.
"""

from myntos.integrations.skatteetaten.client import (
    SkatteetatenError,
    SkatteetatenNotConfiguredError,
    SkattemeldingClient,
    ValidationResult,
)
from myntos.integrations.skatteetaten.mva import (
    MvaError,
    MvaFeedback,
    MvaMeldingClient,
    MvaNotConfiguredError,
    MvaValidation,
)

__all__ = [
    "MvaError",
    "MvaFeedback",
    "MvaMeldingClient",
    "MvaNotConfiguredError",
    "MvaValidation",
    "SkatteetatenError",
    "SkatteetatenNotConfiguredError",
    "SkattemeldingClient",
    "ValidationResult",
]
